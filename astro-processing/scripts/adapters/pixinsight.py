"""PixInsight discovery and constrained GUI/PJSR execution adapter."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Any

from .common import command, executable, platform_key


def find_pixinsight(explicit: str | None = None) -> Path | None:
    home = Path.home()
    return executable([
        explicit,
        os.environ.get("PIXINSIGHT_EXECUTABLE"),
        command("PixInsight"),
        "/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight",
        "/Applications/PixInsight.app/Contents/MacOS/PixInsight",
        home / "Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight",
        "/opt/PixInsight/bin/PixInsight",
    ])


def version_banner(path: Path) -> tuple[str, str]:
    """Read the core banner. PixInsight may return nonzero after printing it."""
    plist = path.parents[1] / "Info.plist" if len(path.parents) >= 2 else None
    if plist and plist.is_file():
        try:
            with plist.open("rb") as stream:
                metadata = plistlib.load(stream)
            version = str(metadata.get("CFBundleShortVersionString", "unknown"))
            return version, f"PixInsight Core {version} (macOS application metadata; build requires PJSR)"
        except (OSError, plistlib.InvalidFileException):
            pass
    try:
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=8, check=False
        )
        raw = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", str(exc)
    match = re.search(r"PixInsight Core\s+(\d+\.\d+\.\d+)(?:\s+([^\r\n]+))?", raw)
    if not match:
        return "unknown", raw
    codename_arch = (match.group(2) or "").strip()
    return match.group(1), f"PixInsight Core {match.group(1)} {codename_arch}".strip()


def _stage_maturity() -> dict[str, str]:
    observed = "experimental"
    return {
        "inspect": observed,
        "background_extraction": observed,
        "color_calibration": observed,
        "deconvolution": observed,
        "star_separation": observed,
        "denoise": observed,
        "stretch": observed,
        "star_reconstruction": observed,
        "export": observed,
        "fits_boundary": "experimental",
        "image_solver": "experimental",
        "spcc": "experimental",
        "dbe": "experimental",
        "calibration": "experimental",
        "registration": "experimental",
        "integration": "experimental",
        "wbpp": "experimental",
    }


def discover(explicit: str | None = None) -> dict[str, Any]:
    path = find_pixinsight(explicit)
    if not path:
        return {"available": False, "installed": False, "maturity": "unavailable", "stages": {}}
    version, raw = version_banner(path)
    platform = platform_key()
    return {
        "available": True,
        "installed": True,
        "executable": str(path),
        "version": version,
        "build": "requires-pjsr-probe",
        "raw_version": raw,
        "maturity": "experimental",
        "platform": platform,
        "execution": "running-gui-ipc-pjsr",
        "headless": False,
        "note": "--execute submits PJSR to a running GUI instance; result JSON, not launcher exit status, is authoritative.",
        "validated_scope": "See references/pixinsight.md for the exact, single-host validation matrix; discovery alone never proves a stage.",
        "stages": _stage_maturity(),
    }
