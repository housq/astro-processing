"""Experimental SETI Astro Suite Pro Cosmic Clarity CLI adapter."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .common import command, executable, platform_key


ROUTABLE_STAGES = {"detail_restoration", "satellite_removal"}
AB_ONLY_STAGES = {"denoise", "star_separation"}
MODEL_SENTINELS = {
    "detail_restoration": ("deep_sharp_stellar_AI4.pth",),
    "satellite_removal": ("satelliteRemovalAI4.pth",),
    "denoise": ("deep_denoise_mono_AI4.pth", "deep_denoise_color_AI4.pth"),
    "star_separation": ("darkstar_mono_AI4.pt", "darkstar_color_AI4.pt"),
}


class SetiAstroError(RuntimeError):
    pass


def _probe(prefix: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [*prefix, "--help"], text=True, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    raw = (completed.stdout + "\n" + completed.stderr).strip()
    required = ("sharpen", "satellite")
    return completed.returncode == 0 and all(token in raw.lower() for token in required), raw


def _version(raw: str) -> str:
    match = re.search(r"(?:SASpro|SetiAstroSuitePro)[^\d]*(\d+\.\d+(?:\.\d+)*)", raw, re.IGNORECASE)
    return match.group(1) if match else "unknown"


def _models_root() -> Path | None:
    explicit = os.environ.get("SASPRO_MODELS_DIR")
    if explicit:
        root = Path(explicit).expanduser()
        return root.resolve() if root.is_dir() else None
    runtime_override = os.environ.get("SASPRO_RUNTIME_DIR")
    if runtime_override:
        base = Path(runtime_override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/SASpro/runtime"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "SASpro/runtime"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "SASpro/runtime"
    candidates = sorted(base.glob("py*/models"), reverse=True) if base.is_dir() else []
    return candidates[0].resolve() if candidates else None


def discover_models() -> dict[str, Any]:
    root = _models_root()
    result: dict[str, Any] = {"root": str(root) if root else None, "stages": {}, "missing": {}}
    for stage, sentinels in MODEL_SENTINELS.items():
        missing = [name for name in sentinels if not root or not (root / name).is_file()]
        if missing:
            result["missing"][stage] = missing
        else:
            result["stages"][stage] = [str((root / name).resolve()) for name in sentinels]  # type: ignore[operator]
    return result


def discover(explicit: str | None = None) -> dict[str, Any]:
    home = Path.home()
    path = executable([
        explicit,
        os.environ.get("SETIASTRO_CLI"),
        os.environ.get("SASPRO_CLI"),
        command("cosmicclarity"),
        command("setiastrosuitepro"),
        command("saspro"),
        "/Applications/SASpro.app/Contents/MacOS/SASpro",
        home / "Applications/SASpro.app/Contents/MacOS/SASpro",
    ])
    prefixes: list[list[str]] = []
    if path:
        prefixes.append([str(path), "cc"] if path.name.lower() != "cosmicclarity" else [str(path)])
    if not explicit:
        try:
            module_available = importlib.util.find_spec("setiastro.saspro") is not None
        except ModuleNotFoundError:
            module_available = False
        if module_available:
            prefixes.append([sys.executable, "-m", "setiastro.saspro", "cc"])
    for prefix in prefixes:
        ok, raw = _probe(prefix)
        if ok:
            models = discover_models()
            stages = {
                stage: "experimental"
                for stage in sorted(ROUTABLE_STAGES)
                if stage in models["stages"]
            }
            ab_stages = {
                stage: "experimental"
                for stage in sorted(AB_ONLY_STAGES)
                if stage in models["stages"]
            }
            return {
                "available": bool(stages or ab_stages),
                "installed": True,
                "executable": str(path) if path else sys.executable,
                "command_prefix": prefix,
                "version": _version(raw),
                "maturity": "experimental",
                "platform": platform_key(),
                "models": models,
                "stages": stages,
                "ab_stages": ab_stages,
                "super_resolution": "not-exposed",
            }
    return {
        "available": False,
        "installed": bool(path or prefixes),
        "executable": str(path) if path else None,
        "maturity": "unavailable",
        "stages": {},
        "ab_stages": {},
        "super_resolution": "not-exposed",
    }


def default_params(stage: str) -> dict[str, Any]:
    common = {"gpu": True, "chunk_size": 256, "overlap": 64, "linear": True}
    if stage == "detail_restoration":
        return {
            **common,
            "sharpening_mode": "Non-Stellar Only",
            "stellar_amount": 0.25,
            "nonstellar_amount": 0.35,
            "nonstellar_psf": 3.0,
        }
    if stage == "satellite_removal":
        return {**common, "mode": "full", "clip_trail": True, "sensitivity": 0.10}
    if stage == "denoise":
        return {**common, "denoise_luma": 0.35, "denoise_color": 0.35, "denoise_mode": "full"}
    if stage == "star_separation":
        return {
            "gpu": True,
            "chunk_size": 512,
            "overlap_frac": 0.125,
            "star_removal_mode": "unscreen",
            "processing_path": "hybrid_luma_color",
            "edge_padding": 64,
        }
    raise SetiAstroError(f"Unsupported SETI Astro stage: {stage}")


def _bool_option(name: str, value: bool) -> str:
    return name if value else f"--no-{name.removeprefix('--')}"


def build_command(
    capability: dict[str, Any],
    stage: str,
    input_path: Path,
    output_path: Path,
    params: dict[str, Any],
    *,
    ab_candidate: bool = False,
) -> tuple[list[str], dict[str, Path]]:
    if stage not in ROUTABLE_STAGES and not (ab_candidate and stage in AB_ONLY_STAGES):
        raise SetiAstroError(
            "SETI Astro only routes detail_restoration and satellite_removal; denoise/star separation require --ab-candidate"
        )
    prefix = capability.get("command_prefix")
    if not isinstance(prefix, list) or not prefix:
        raise SetiAstroError("SETI Astro Cosmic Clarity CLI is unavailable")
    values = default_params(stage)
    values.update(params)
    common = [
        "-i", str(input_path), "-o", str(output_path),
        _bool_option("--gpu", bool(values.get("gpu", True))),
        "--chunk-size", str(int(values.get("chunk_size", 256))),
    ]
    if stage != "star_separation":
        common += ["--overlap", str(int(values.get("overlap", 64)))]
    if stage == "detail_restoration":
        command_line = [*prefix, "sharpen", *common]
        if values.get("linear", True):
            command_line.append("--temp-stretch")
        command_line += [
            "--sharpening-mode", str(values["sharpening_mode"]),
            "--stellar-amount", f"{float(values['stellar_amount']):g}",
            "--nonstellar-amount", f"{float(values['nonstellar_amount']):g}",
            "--nonstellar-psf", f"{float(values['nonstellar_psf']):g}",
        ]
    elif stage == "satellite_removal":
        sensitivity = float(values["sensitivity"])
        if not 0 <= sensitivity <= 1:
            raise SetiAstroError("SETI Astro satellite sensitivity must be between 0 and 1")
        command_line = [
            *prefix, "satellite", *common,
            "--mode", str(values["mode"]),
            _bool_option("--clip-trail", bool(values["clip_trail"])),
            "--sensitivity", f"{sensitivity:g}",
        ]
    elif stage == "denoise":
        command_line = [*prefix, "denoise", *common]
        if values.get("linear", True):
            command_line.append("--temp-stretch")
        command_line += [
            "--denoise-luma", f"{float(values['denoise_luma']):g}",
            "--denoise-color", f"{float(values['denoise_color']):g}",
            "--denoise-mode", str(values["denoise_mode"]),
        ]
    else:
        command_line = [
            *prefix, "darkstar", *common,
            "--overlap-frac", f"{float(values['overlap_frac']):g}",
            "--star-removal-mode", str(values["star_removal_mode"]),
            "--processing-path", str(values["processing_path"]),
            "--edge-padding", str(int(values["edge_padding"])),
        ]
    # Deliberately no superres builder.  This adapter never routes or exposes it.
    return command_line, {"starless" if stage == "star_separation" else "primary": output_path}
