"""RC-Astro CLI discovery adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .common import command, executable, platform_key


def discover() -> dict[str, Any]:
    path = executable([
        os.environ.get("RC_ASTRO_CLI"),
        command("rc-astro"),
        "/usr/local/bin/rc-astro",
        "/opt/homebrew/bin/rc-astro",
        Path.home() / ".local/share/astro-processing/tools/rc-astro",
    ])
    if not path:
        return {"available": False, "installed": False, "maturity": "unavailable", "license": "unknown", "stages": {}}
    return {
        "available": False,
        "installed": True,
        "executable": str(path),
        "version": "unknown",
        "maturity": "experimental",
        "license": "unverified",
        "platform": platform_key(),
        "note": "Each BXT/NXT/SXT license must be confirmed by an adapter smoke test before routing.",
        "stages": {
            "deconvolution": "experimental",
            "denoise": "experimental",
            "star_separation": "experimental",
        },
    }
