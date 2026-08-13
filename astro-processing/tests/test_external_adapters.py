#!/usr/bin/env python3
"""Dependency-free tests for optional processor adapters."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from adapters import graxpert, setiastro, starnet  # noqa: E402


def executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_current_starnet_is_self_contained() -> None:
    with tempfile.TemporaryDirectory() as temp:
        fake = executable(
            Path(temp) / "starnet2",
            "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 'starnet2 version: 2.5.4'; else echo 'StarNet2 CLI, CoreML backend --unscreen'; fi\n",
        )
        result = starnet.discover(str(fake), str(Path(temp) / "missing.onnx"))
        assert result["available"] is True
        assert result["interface"] == "current-self-contained"
        assert result["requires_weights"] is False
        assert result["version"] == "2.5.4"
        assert result["runtime_backend"] == "coreml"


def test_legacy_starnet_uses_weights_and_siril_contract() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fake = executable(
            root / "starnet2",
            "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 'starnet2 version: 2.1.0'; else echo 'CLI interface for StarNet2 w/ Torch backend'; fi\n",
        )
        weights = root / "StarNet2_weights.pt"
        weights.write_bytes(b"weights")
        result = starnet.discover(str(fake), str(weights))
        assert result["available"] is True
        assert result["interface"] == "legacy-weights"
        assert result["requires_weights"] is True
        script, outputs = starnet.build_siril_script(root / "input.fit", root / "attempt", True)
        assert "starnet -stretch" in script
        assert outputs["starless"].name == "starless_source-linear.fit"
        assert outputs["stars"].name == "starmask_source-linear.fit"


def test_graxpert_never_implicitly_downloads_a_model() -> None:
    capability = {"executable": "/fake/GraXpert", "models": {}}
    try:
        graxpert.build_command(
            capability, "background_extraction", Path("input.fit"), Path("result"), {}
        )
    except graxpert.GraXpertError as exc:
        assert "implicit model download" in str(exc)
    else:
        raise AssertionError("GraXpert command allowed an unapproved implicit model download")


def test_setiastro_exposes_only_bounded_route_stages() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fake = executable(
            root / "setiastrosuitepro",
            "#!/bin/sh\necho 'usage: setiastrosuitepro cc {sharpen,correct,denoise,both,superres,satellite,darkstar}'\n",
        )
        models = root / "models"
        models.mkdir()
        for name in (
            "deep_sharp_stellar_AI4.pth", "satelliteRemovalAI4.pth",
            "deep_denoise_mono_AI4.pth", "deep_denoise_color_AI4.pth",
            "darkstar_mono_AI4.pt", "darkstar_color_AI4.pt",
        ):
            (models / name).write_bytes(b"model")
        previous = os.environ.get("SASPRO_MODELS_DIR")
        os.environ["SASPRO_MODELS_DIR"] = str(models)
        try:
            capability = setiastro.discover(str(fake))
        finally:
            if previous is None:
                os.environ.pop("SASPRO_MODELS_DIR", None)
            else:
                os.environ["SASPRO_MODELS_DIR"] = previous
        assert capability["available"] is True
        assert set(capability["stages"]) == {"detail_restoration", "satellite_removal"}
        assert set(capability["ab_stages"]) == {"denoise", "star_separation"}
        assert capability["super_resolution"] == "not-exposed"
        command, outputs = setiastro.build_command(
            capability,
            "detail_restoration",
            root / "input.fit",
            root / "output.fit",
            {},
        )
        assert command[:3] == [str(fake.resolve()), "cc", "sharpen"]
        assert "--temp-stretch" in command
        assert outputs["primary"].name == "output.fit"
        try:
            setiastro.build_command(
                capability,
                "denoise",
                root / "input.fit",
                root / "output.fit",
                {},
            )
        except setiastro.SetiAstroError as exc:
            assert "--ab-candidate" in str(exc)
        else:
            raise AssertionError("SETI denoise was exposed without explicit A/B mode")


def test_setiastro_does_not_route_without_local_models() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fake = executable(
            root / "setiastrosuitepro",
            "#!/bin/sh\necho 'usage: setiastrosuitepro cc {sharpen,satellite,darkstar,denoise}'\n",
        )
        empty_models = root / "empty-models"
        empty_models.mkdir()
        previous = os.environ.get("SASPRO_MODELS_DIR")
        os.environ["SASPRO_MODELS_DIR"] = str(empty_models)
        try:
            capability = setiastro.discover(str(fake))
        finally:
            if previous is None:
                os.environ.pop("SASPRO_MODELS_DIR", None)
            else:
                os.environ["SASPRO_MODELS_DIR"] = previous
        assert capability["installed"] is True
        assert capability["available"] is False
        assert capability["stages"] == {}
        assert "deep_sharp_stellar_AI4.pth" in capability["models"]["missing"]["detail_restoration"]


def main() -> None:
    tests = [
        test_current_starnet_is_self_contained,
        test_legacy_starnet_uses_weights_and_siril_contract,
        test_graxpert_never_implicitly_downloads_a_model,
        test_setiastro_exposes_only_bounded_route_stages,
        test_setiastro_does_not_route_without_local_models,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
