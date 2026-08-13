"""StarNet optional-processor adapter for current and legacy CLI packages."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .common import command, executable, maturity, platform_key


VALIDATED_PLATFORMS = {"darwin-arm64"}
CURRENT_MIN_VERSION = (2, 5, 0)


class StarNetError(RuntimeError):
    pass


def _probe(path: Path) -> tuple[tuple[int, ...] | None, str, str]:
    chunks: list[str] = []
    for option in ("--version", "--help"):
        try:
            completed = subprocess.run(
                [str(path), option], text=True, capture_output=True, timeout=20, check=False
            )
            chunks.append((completed.stdout + "\n" + completed.stderr).strip())
        except (OSError, subprocess.SubprocessError):
            continue
    raw = "\n".join(item for item in chunks if item)
    match = re.search(r"(?:StarNet(?:\+\+|2)?\s*v?|version:\s*)(\d+)\.(\d+)(?:\.(\d+))?", raw, re.IGNORECASE)
    version = tuple(int(part or 0) for part in match.groups()) if match else None
    current = bool(version and version >= CURRENT_MIN_VERSION) or "--unscreen" in raw or "ONNX Runtime backend" in raw or "CoreML" in raw
    interface = "current-self-contained" if current else "legacy-weights"
    backend = "coreml" if "CoreML" in raw else "onnx-runtime" if "ONNX" in raw else "torch" if "Torch" in raw else "unknown"
    return version, interface, backend


def discover(explicit: str | None = None, explicit_weights: str | None = None) -> dict[str, Any]:
    home = Path.home()
    path = executable([
        explicit,
        os.environ.get("STARNET_CLI"),
        command("starnet2"),
        command("starnet++"),
        command("starnet"),
        home / "StarNet2T_MacOS/starnet2",
        home / ".local/share/astro-processing/tools/starnet2",
    ])
    weights_candidates: list[str | Path | None] = [
        explicit_weights,
        os.environ.get("STARNET_WEIGHTS"),
        path.parent / "StarNet2_weights.pt" if path else None,
        home / "StarNet2T_MacOS/StarNet2_weights.pt",
        home / ".local/share/astro-processing/models/starnet/StarNet2_weights.pt",
    ]
    weights = next((Path(item).expanduser().resolve() for item in weights_candidates if item and Path(item).expanduser().is_file()), None)
    if not path:
        return {
            "available": False,
            "installed": False,
            "maturity": "unavailable",
            "executable": None,
            "weights": str(weights) if weights else None,
            "stages": {},
        }
    version, interface, backend = _probe(path)
    requires_weights = interface == "legacy-weights"
    available = not requires_weights or bool(weights)
    # The legacy macOS ARM64 lane has been exercised end-to-end.  New
    # self-contained packages are supported by the adapter but remain
    # experimental until their exact build passes a smoke test on the host.
    state = maturity(VALIDATED_PLATFORMS) if interface == "legacy-weights" else "experimental"
    if not available:
        state = "unavailable"
    return {
        "available": available,
        "installed": True,
        "executable": str(path),
        "weights": str(weights) if requires_weights and weights else None,
        "requires_weights": requires_weights,
        "interface": interface,
        "runtime_backend": backend,
        "version": ".".join(map(str, version)) if version else "unknown",
        "maturity": state,
        "platform": platform_key(),
        "stages": {"star_separation": state} if available else {},
    }


def siril_config_values(capability: dict[str, Any]) -> dict[str, str]:
    executable_path = capability.get("executable")
    if not executable_path:
        raise StarNetError("StarNet executable is unavailable")
    if capability.get("requires_weights") and not capability.get("weights"):
        raise StarNetError("This legacy StarNet build requires a model weights file")
    return {
        "starnet_exe": str(executable_path),
        "starnet_weights": str(capability.get("weights") or "") if capability.get("requires_weights") else "",
    }


def _ssf_quote(value: Path) -> str:
    text = str(value).replace("\\", "/")
    if any(character in text for character in ('"', "\n", "\r")):
        raise StarNetError(f"Path cannot be represented safely in a Siril script: {text!r}")
    return f'"{text}"'


def build_siril_script(input_path: Path, attempt_dir: Path, linear: bool = True) -> tuple[str, dict[str, Path]]:
    source = attempt_dir / "source-linear"
    lines = [
        "requires 1.4.0",
        "setext fit",
        f"cd {_ssf_quote(attempt_dir)}",
        f"load {_ssf_quote(input_path)}",
        f"save {_ssf_quote(source)}",
        "starnet -stretch" if linear else "starnet",
        "close",
    ]
    outputs = {
        "starless": attempt_dir / "starless_source-linear.fit",
        "stars": attempt_dir / "starmask_source-linear.fit",
    }
    return "\n".join(lines) + "\n", outputs
