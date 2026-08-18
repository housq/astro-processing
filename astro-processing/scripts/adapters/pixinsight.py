"""PixInsight discovery and constrained GUI/PJSR execution adapter."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Any

from .common import command, executable, platform_key


RC_ASTRO_MODULES = {
    "bxt": {
        "filename": "BlurXTerminator-pxm.dylib",
        "process": "BlurXTerminator",
        "model_token": "BlurXTerminator.mlpackage",
        "expected_model": "BlurXTerminator.4.mlpackage",
        "stage": "deconvolution",
    },
    "sxt": {
        "filename": "StarXTerminator-pxm.dylib",
        "process": "StarXTerminator",
        "model_token": "StarXTerminator.mlpackage",
        "expected_model": "StarXTerminator.lite.nonoise.11.mlpackage",
        "stage": "star_separation",
    },
    "nxt": {
        "filename": "NoiseXTerminator-pxm.dylib",
        "process": "NoiseXTerminator",
        "model_token": "NoiseXTerminator.mlpackage",
        "expected_model": "NoiseXTerminator.3.mlpackage",
        "stage": "denoise",
    },
}


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


def _module_directories(executable_path: Path) -> list[Path]:
    """Return installation-relative module directories without searching user data."""
    candidates = [executable_path.parent / "bin", executable_path.parent]
    parents = list(executable_path.parents)
    if len(parents) >= 4 and parents[1].name == "Contents":
        candidates.extend((parents[3] / "bin", parents[1] / "bin"))
    result: list[Path] = []
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate not in result:
            result.append(candidate)
    return result


def discover_rc_astro(executable_path: Path | None) -> dict[str, Any]:
    """Discover PI modules, version markers, and bundled-model tokens.

    This is deliberately independent of license confirmation. A discovered
    module is not routable until an authenticated PJSR capability probe also
    proves that the process can be constructed in the frozen PixInsight build.
    """
    modules: dict[str, Any] = {}
    for key, specification in RC_ASTRO_MODULES.items():
        module_path = None
        if executable_path:
            for directory in _module_directories(executable_path):
                candidate = directory / specification["filename"]
                if candidate.is_file():
                    module_path = candidate
                    break
        record: dict[str, Any] = {
            "installed": False,
            "version": None,
            "model_token": specification["model_token"],
            "expected_model": specification["expected_model"],
            "model_detected": False,
            "process": specification["process"],
            "stage": specification["stage"],
        }
        if module_path:
            try:
                payload = module_path.read_bytes()
            except OSError as exc:
                record["error"] = str(exc)
            else:
                match = re.search(rb"PIXINSIGHT_MODULE_VERSION_(\d+)\.(\d+)\.(\d+)\.(\d+)\.", payload)
                record.update({
                    "installed": match is not None,
                    "module": str(module_path),
                    "version": ".".join(part.decode("ascii") for part in match.groups()[:3]) if match else None,
                    "model_detected": specification["model_token"].encode("ascii") in payload,
                })
        modules[key] = record
    installed = all(item["installed"] and item["model_detected"] and item["version"] for item in modules.values())
    return {
        "available": False,
        "installed": installed,
        "license": "unconfirmed",
        "runtime_probe": "required",
        "maturity": "experimental" if installed else "unavailable",
        "provider": "PixInsight process modules",
        "modules": modules,
        "stages": {
            specification["stage"]: "experimental" if modules[key]["installed"] else "unavailable"
            for key, specification in RC_ASTRO_MODULES.items()
        },
        "note": "Filesystem discovery does not confirm a license or runtime process availability.",
    }


def discover(explicit: str | None = None) -> dict[str, Any]:
    path = find_pixinsight(explicit)
    if not path:
        return {"available": False, "installed": False, "maturity": "unavailable", "stages": {}}
    version, raw = version_banner(path)
    platform = platform_key()
    result = {
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
    result["rc_astro_modules"] = discover_rc_astro(path)
    return result
