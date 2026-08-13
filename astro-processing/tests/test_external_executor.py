#!/usr/bin/env python3
"""Dependency-free integration tests for routed processor attempts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import external_executor  # noqa: E402
from adapters import siril_engine  # noqa: E402
from config import write_yaml  # noqa: E402


GROUP_ID = "g-test"


def fits_card(key: str, value: str) -> bytes:
    return f"{key:<8}= {value:<70}"[:80].encode("ascii")


def write_test_fits(path: Path) -> None:
    cards = [
        fits_card("SIMPLE", "T"),
        fits_card("BITPIX", "-32"),
        fits_card("NAXIS", "3"),
        fits_card("NAXIS1", "2"),
        fits_card("NAXIS2", "2"),
        fits_card("NAXIS3", "3"),
        b"END".ljust(80),
    ]
    header = b"".join(cards).ljust(2880, b" ")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"\0" * 2880)


def executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def make_run(root: Path, graxpert_path: Path) -> tuple[Path, Path]:
    run = root / "run"
    group_root = run / "groups" / GROUP_ID
    linear = group_root / "checkpoints" / "stacked-linear.fit"
    write_test_fits(linear)
    for name in ("inputs", "process", "masters", "attempts", "external", "logs", "scripts"):
        (group_root / name).mkdir(parents=True, exist_ok=True)
    siril_engine.atomic_json(run / ".siril-run.json", {
        "run_id": "test-run",
        "run_dir": str(run.resolve()),
        "input_dir": str((root / "input").resolve()),
        "created_at": siril_engine.now_utc(),
    })
    siril_engine.atomic_json(run / ".astro-run.json", {
        "run_dir": str(run.resolve()),
        "run_config": str(run / "run-config.yaml"),
        "route_fingerprint": "test-route",
    })
    siril_engine.atomic_json(run / "plan.json", {
        "counts": {"light": 2},
        "groups": [{
            "id": GROUP_ID,
            "target": "test",
            "session_date": "2026-01-01",
            "lights": ["a.fit", "b.fit"],
            "calibrations": {"bias": [], "dark": [], "flat": [], "darkflat": []},
            "warnings": [],
        }],
        "quarantined": [],
        "warnings": [],
    })
    siril_engine.atomic_json(run / "state.json", {
        "schema_version": 1,
        "created_at": siril_engine.now_utc(),
        "updated_at": siril_engine.now_utc(),
        "groups": {GROUP_ID: {
            "preprocess": "complete",
            "linear_checkpoint": str(linear),
            "attempts": [],
            "selected_attempt": None,
        }},
    })
    write_yaml(run / "run-config.yaml", {
        "project_root": str(root),
        "capabilities": {"siril": {"executable": "/bin/true"}},
        "resolved_routing": {
            "main_backend": {"selected": "siril"},
            "processors": {
                "background_extraction": {
                    "selected": "graxpert", "fallbacks": ["main"], "maturity": "validated"
                },
                "denoise": {"selected": "graxpert", "fallbacks": ["main"], "maturity": "validated"},
                "star_separation": {"selected": "disabled", "fallbacks": []},
                "detail_restoration": {"selected": "disabled", "fallbacks": []},
                "satellite_removal": {"selected": "disabled", "fallbacks": []},
            },
        },
    })
    return run, linear


def test_real_command_attempt_and_visual_promotion() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fake = executable(
            root / "GraXpert",
            """#!/bin/sh
input="$1"
shift
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-output" ]; then output="$2"; shift 2; continue; fi
  if [ "$1" = "-bg" ]; then background=true; shift; continue; fi
  shift
done
cp "$input" "${output}.fits"
if [ "$background" = "true" ]; then cp "$input" "${output}_background.fits"; fi
""",
        )
        model_root = root / "models"
        model = model_root / "bge-ai-models" / "1.0.1" / "model.onnx"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"model")
        run, linear = make_run(root, fake)
        old_cli = os.environ.get("GRAXPERT_CLI")
        old_models = os.environ.get("GRAXPERT_MODELS_DIR")
        old_preview = external_executor._preview_outputs
        os.environ["GRAXPERT_CLI"] = str(fake)
        os.environ["GRAXPERT_MODELS_DIR"] = str(model_root)

        def fake_preview(_run, _siril, attempt_dir, outputs, _linear):
            previews = {}
            stats = {}
            for role in outputs:
                preview = attempt_dir / f"preview-{role}.png"
                stat = attempt_dir / f"stats-{role}.json"
                preview.write_bytes(b"png")
                stat.write_text("{}\n", encoding="utf-8")
                previews[role] = str(preview)
                stats[role] = str(stat)
            return previews, stats

        external_executor._preview_outputs = fake_preview
        try:
            attempt_dir = external_executor.run_stage(
                run, GROUP_ID, "background_extraction", None, None, None, [], "", False, True, "/bin/true", False
            )
            assert (attempt_dir / "result.fits").is_file()
            state = siril_engine.load_json(run / "state.json")
            attempt = state["groups"][GROUP_ID]["processor_attempts"][0]
            assert attempt["status"] == "needs-review"
            assert attempt["processor"] == "graxpert"
            assert attempt["input_checkpoint"] == str(linear.resolve())
            external_executor.review_stage(
                run, GROUP_ID, attempt["id"], "accept", "Background model contains no target signal.", [], []
            )
            state = siril_engine.load_json(run / "state.json")
            group_state = state["groups"][GROUP_ID]
            assert group_state["active_linear_checkpoint"] == str(attempt_dir / "result.fits")
            assert group_state["accepted_processor_attempts"]["background_extraction"] == attempt["id"]
        finally:
            external_executor._preview_outputs = old_preview
            if old_cli is None:
                os.environ.pop("GRAXPERT_CLI", None)
            else:
                os.environ["GRAXPERT_CLI"] = old_cli
            if old_models is None:
                os.environ.pop("GRAXPERT_MODELS_DIR", None)
            else:
                os.environ["GRAXPERT_MODELS_DIR"] = old_models


def test_rejected_review_requires_evidence() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fake = executable(root / "GraXpert", "#!/bin/sh\nexit 0\n")
        run, _ = make_run(root, fake)
        state = siril_engine.load_json(run / "state.json")
        state["groups"][GROUP_ID]["processor_attempts"] = [{
            "id": "background-extraction-001",
            "stage": "background_extraction",
            "processor": "graxpert",
            "status": "needs-review",
            "outputs": {"primary": str(root / "candidate.fit")},
            "input_checkpoint": str(root / "input.fit"),
        }]
        siril_engine.atomic_json(run / "state.json", state)
        try:
            external_executor.review_stage(
                run, GROUP_ID, "background-extraction-001", "reject", "", [], []
            )
        except external_executor.ExternalExecutionError as exc:
            assert "record notes" in str(exc)
        else:
            raise AssertionError("Rejected review accepted without evidence")


def main() -> None:
    tests = [test_real_command_attempt_and_visual_promotion, test_rejected_review_requires_evidence]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
