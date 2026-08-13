"""PixInsight discovery and phase-one planning adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .common import command, executable, platform_key


def discover() -> dict[str, Any]:
    home = Path.home()
    path = executable([
        os.environ.get("PIXINSIGHT_EXECUTABLE"),
        command("PixInsight"),
        "/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight",
        "/Applications/PixInsight.app/Contents/MacOS/PixInsight",
        home / "Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight",
        "/opt/PixInsight/bin/PixInsight",
    ])
    if not path:
        return {"available": False, "installed": False, "maturity": "unavailable", "stages": {}}
    return {
        "available": True,
        "installed": True,
        "executable": str(path),
        "version": "unknown",
        "maturity": "experimental",
        "platform": platform_key(),
        "execution": "pjsr-planning-only",
        "note": "PJSR post-processing and WBPP adapters require forward validation before automatic execution.",
        "stages": {
            "background_extraction": "experimental",
            "color_calibration": "experimental",
            "stretch": "experimental",
            "export": "experimental",
            "calibration": "experimental",
            "registration": "experimental",
            "integration": "experimental",
        },
    }
