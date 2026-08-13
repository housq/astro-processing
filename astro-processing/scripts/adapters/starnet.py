"""StarNet optional-processor adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .common import command, executable, maturity, platform_key


VALIDATED_PLATFORMS = {"darwin-arm64"}


def discover() -> dict[str, Any]:
    home = Path.home()
    path = executable([
        os.environ.get("STARNET_CLI"),
        command("starnet2"),
        command("starnet++"),
        command("starnet"),
        home / "StarNet2T_MacOS/starnet2",
        home / ".local/share/astro-processing/tools/starnet2",
    ])
    weights_candidates = [
        os.environ.get("STARNET_WEIGHTS"),
        path.parent / "StarNet2_weights.pt" if path else None,
        home / "StarNet2T_MacOS/StarNet2_weights.pt",
        home / ".local/share/astro-processing/models/starnet/StarNet2_weights.pt",
    ]
    weights = next((Path(item).expanduser().resolve() for item in weights_candidates if item and Path(item).expanduser().is_file()), None)
    if not path or not weights:
        return {
            "available": False,
            "installed": bool(path),
            "maturity": "unavailable",
            "executable": str(path) if path else None,
            "weights": str(weights) if weights else None,
            "stages": {},
        }
    state = maturity(VALIDATED_PLATFORMS)
    return {
        "available": True,
        "installed": True,
        "executable": str(path),
        "weights": str(weights),
        "version": "2.x",
        "maturity": state,
        "platform": platform_key(),
        "stages": {"star_separation": state},
    }
