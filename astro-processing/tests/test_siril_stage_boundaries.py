#!/usr/bin/env python3
"""Regression tests for bounded Siril post-processing attempts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from adapters import siril_engine  # noqa: E402


def test_color_attempt_stops_before_denoise_and_stretch() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        script = siril_engine.postprocess_script(
            root / "background.fit",
            root / "attempt",
            siril_engine.default_postprocess_params(),
            "color",
            "color",
        )
        assert "pcc -catalog=apass" in script
        assert "color.fit" in script
        assert "denoise -mod=" not in script
        assert "autostretch" in script  # linear preview only
        assert "stretch.fit" not in script
        assert "final.jpg" not in script


def test_end_stage_cannot_precede_start_stage() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        try:
            siril_engine.postprocess_script(
                root / "color.fit",
                root / "attempt",
                siril_engine.default_postprocess_params(),
                "stretch",
                "color",
            )
        except siril_engine.PipelineError as exc:
            assert "precedes" in str(exc)
        else:
            raise AssertionError("Invalid reversed stage boundary was accepted")


def main() -> None:
    tests = [test_color_attempt_stops_before_denoise_and_stretch, test_end_stage_cannot_precede_start_stage]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
