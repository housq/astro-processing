#!/usr/bin/env python3
"""Dependency-free PixInsight adapter and PJSR generation tests."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from adapters import pixinsight_engine  # noqa: E402
from astro import main as astro_main  # noqa: E402


def fake_xisf(root: Path) -> Path:
    path = root / "integrated-linear.xisf"
    path.write_bytes(b"test-only-placeholder")
    return path


def test_declared_input_contract() -> None:
    with tempfile.TemporaryDirectory() as temp:
        path = fake_xisf(Path(temp))
        profile, blockers = pixinsight_engine.inspect_declared_input(path, "integrated-linear", "osc-color")
        assert not blockers
        assert profile["declared_linear"] is True
        assert profile["declared_data_type"] == "osc-color"


def test_unknown_linearity_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as temp:
        path = fake_xisf(Path(temp))
        _, blockers = pixinsight_engine.inspect_declared_input(path, "unknown", "osc-color")
        assert any(item["code"] == "PIXINSIGHT_INPUT_STATE_REQUIRED" for item in blockers)


def test_dry_run_generates_relocatable_pjsr_without_pixinsight() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = fake_xisf(root)
        run_dir = pixinsight_engine.create_run(source, root / "output", "dry-run", True, True)
        result = pixinsight_engine.execute(run_dir, "/not/installed/PixInsight", 1, True)
        script = Path(result["script"]).read_text(encoding="utf-8")
        params = json.loads((run_dir / "params.json").read_text(encoding="utf-8"))
        assert result["dry_run"] is True
        assert "__ASTRO_PARAMS_JSON__" not in script
        assert str(run_dir / "params.json") in script
        assert "new BlurXTerminator" in script
        assert "new StarXTerminator" in script
        assert "new NoiseXTerminator" in script
        assert params["stages"]["exports"]["fits32"] is True
        assert params["stages"]["rc_astro"]["license_confirmed"] is True


def test_unified_run_dispatches_pixinsight_dry_run() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = fake_xisf(root)
        run_dir = pixinsight_engine.create_run(source, root / "output", "dispatch", False, False)
        marker = {"schema_version": 1, "main_backend": "pixinsight", "run_dir": str(run_dir)}
        (run_dir / ".astro-run.json").write_text(json.dumps(marker), encoding="utf-8")
        assert astro_main(["run", "--run", str(run_dir), "--dry-run"]) == 0


def test_template_contains_boundary_gates() -> None:
    text = pixinsight_engine.PJSR_TEMPLATE.read_text(encoding="utf-8")
    for required in ("sameDimensions", "sameChannels", "bitDepthOK", "wcsPreserved", "orientationOK", "ASTRO_PROCESSING_PIXINSIGHT_OK"):
        assert required in text


def main() -> None:
    tests = [
        test_declared_input_contract,
        test_unknown_linearity_is_blocked,
        test_dry_run_generates_relocatable_pjsr_without_pixinsight,
        test_unified_run_dispatches_pixinsight_dry_run,
        test_template_contains_boundary_gates,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
