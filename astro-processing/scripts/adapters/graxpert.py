"""GraXpert optional-processor adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .common import command, executable, maturity, platform_key


VALIDATED_PLATFORMS = {"darwin-arm64"}


def discover(project_root: Path) -> dict[str, Any]:
    home = Path.home()
    path = executable([
        os.environ.get("GRAXPERT_CLI"),
        command("graxpert"),
        command("GraXpert"),
        project_root / "tools/GraXpert.app/Contents/MacOS/GraXpert",
        "/Applications/GraXpert.app/Contents/MacOS/GraXpert",
        home / "Applications/GraXpert.app/Contents/MacOS/GraXpert",
        home / ".local/share/astro-processing/tools/GraXpert.app/Contents/MacOS/GraXpert",
    ])
    if not path:
        return {"available": False, "installed": False, "maturity": "unavailable", "stages": {}}
    state = maturity(VALIDATED_PLATFORMS)
    return {
        "available": True,
        "installed": True,
        "executable": str(path),
        "version": "unknown",
        "maturity": state,
        "platform": platform_key(),
        "stages": {"background_extraction": state, "denoise": state},
    }
