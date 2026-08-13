"""Siril main-backend adapter."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

from . import siril_engine
from .common import maturity, platform_key


VALIDATED_PLATFORMS = {"darwin-arm64"}


def _stage_maturity(platform_maturity: str) -> dict[str, str]:
    return {
        "inspect": platform_maturity,
        "calibration": platform_maturity,
        "registration": platform_maturity,
        "integration": platform_maturity,
        "background_extraction": platform_maturity,
        "color_calibration": platform_maturity,
        "denoise": platform_maturity,
        "stretch": platform_maturity,
        "export": platform_maturity,
        "deconvolution": "unavailable",
        "star_separation": "unavailable",
    }


def discover(explicit: str | None = None) -> dict[str, Any]:
    try:
        path = siril_engine.find_siril(explicit)
        version, raw = siril_engine.siril_version(path)
        state = maturity(VALIDATED_PLATFORMS)
        compatible = version >= siril_engine.MIN_SIRIL_VERSION
        return {
            "available": compatible,
            "installed": True,
            "executable": str(path),
            "version": ".".join(map(str, version)),
            "maturity": state if compatible else "broken",
            "platform": platform_key(),
            "raw_version": raw,
            "stages": _stage_maturity(state),
        }
    except siril_engine.PipelineError as exc:
        return {"available": False, "installed": False, "maturity": "unavailable", "error": str(exc), "stages": {}}


def inspect_input(path: Path, dark_temp_tolerance: float = 5.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    files = siril_engine.scan_input(path)
    return files, siril_engine.build_plan(path.resolve(), files, dark_temp_tolerance)


def print_inspection(path: Path, files: list[dict[str, Any]], plan: dict[str, Any]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        siril_engine.print_inspection(path, files, plan)
    return buffer.getvalue().rstrip()


def estimate_bytes(plan: dict[str, Any]) -> int:
    return sum(siril_engine.estimate_disk_bytes(group) for group in plan["groups"])


def run(arguments: list[str]) -> int:
    return siril_engine.main(arguments)
