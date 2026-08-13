"""GraXpert optional-processor discovery and command construction."""

from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path
from typing import Any

from .common import command as find_command
from .common import executable, maturity, platform_key


VALIDATED_PLATFORMS = {"darwin-arm64"}
STAGE_MODELS = {
    "background_extraction": "bge-ai-models",
    "denoise": "denoise-ai-models",
}


class GraXpertError(RuntimeError):
    pass


def _version_key(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers) if numbers else (0,)


def _application_version(path: Path) -> str:
    if path.parent.name != "MacOS":
        return "unknown"
    info = path.parent.parent / "Info.plist"
    try:
        with info.open("rb") as handle:
            value = plistlib.load(handle).get("CFBundleShortVersionString")
        return str(value) if value else "unknown"
    except (OSError, plistlib.InvalidFileException):
        return "unknown"


def _model_roots() -> list[Path]:
    home = Path.home()
    roots = [
        Path(os.environ["GRAXPERT_MODELS_DIR"]).expanduser() if os.environ.get("GRAXPERT_MODELS_DIR") else None,
        home / "Library/Application Support/GraXpert",
        home / ".local/share/GraXpert",
        home / ".local/share/graxpert",
        home / ".config/GraXpert",
    ]
    return [root.resolve() for root in roots if root and root.is_dir()]


def discover_models() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for stage, directory_name in STAGE_MODELS.items():
        versions: list[tuple[str, Path]] = []
        for root in _model_roots():
            family = root / directory_name
            if not family.is_dir():
                continue
            for child in family.iterdir():
                model = child / "model.onnx"
                if child.is_dir() and model.is_file():
                    versions.append((child.name, model.resolve()))
        versions.sort(key=lambda item: _version_key(item[0]), reverse=True)
        if versions:
            result[stage] = {
                "selected": versions[0][0],
                "path": str(versions[0][1]),
                "installed": [version for version, _ in versions],
            }
    return result


def discover(project_root: Path) -> dict[str, Any]:
    home = Path.home()
    path = executable([
        os.environ.get("GRAXPERT_CLI"),
        find_command("graxpert"),
        find_command("GraXpert"),
        project_root / "tools/GraXpert.app/Contents/MacOS/GraXpert",
        "/Applications/GraXpert.app/Contents/MacOS/GraXpert",
        home / "Applications/GraXpert.app/Contents/MacOS/GraXpert",
        home / ".local/share/astro-processing/tools/GraXpert.app/Contents/MacOS/GraXpert",
    ])
    if not path:
        return {"available": False, "installed": False, "maturity": "unavailable", "stages": {}}
    models = discover_models()
    state = maturity(VALIDATED_PLATFORMS)
    stages = {
        stage: state if stage in models else "unavailable"
        for stage in STAGE_MODELS
    }
    return {
        "available": any(value != "unavailable" for value in stages.values()),
        "installed": True,
        "executable": str(path),
        "version": _application_version(path),
        "maturity": state,
        "platform": platform_key(),
        "models": models,
        "stages": stages,
    }


def default_params(stage: str) -> dict[str, Any]:
    if stage == "background_extraction":
        return {"gpu": True, "correction": "Subtraction", "smoothing": 0.1, "save_background": True}
    if stage == "denoise":
        return {"gpu": True, "strength": 0.35, "batch_size": 4}
    raise GraXpertError(f"Unsupported GraXpert stage: {stage}")


def build_command(
    capability: dict[str, Any],
    stage: str,
    input_path: Path,
    output_base: Path,
    params: dict[str, Any],
) -> tuple[list[str], dict[str, Path]]:
    executable_path = capability.get("executable")
    if not executable_path:
        raise GraXpertError("GraXpert executable is unavailable")
    model = capability.get("models", {}).get(stage, {})
    model_version = str(params.get("ai_version") or model.get("selected") or "")
    if not model_version:
        raise GraXpertError(
            f"No installed GraXpert model was found for {stage}; do not trigger an implicit model download without user approval"
        )
    installed_versions = set(map(str, model.get("installed", [])))
    if model_version not in installed_versions:
        raise GraXpertError(
            f"GraXpert model {model_version} for {stage} is not installed locally; refusing an implicit download"
        )
    values = default_params(stage)
    values.update(params)
    command_line = [
        str(executable_path),
        str(input_path),
        "-cli",
        "-cmd",
        "background-extraction" if stage == "background_extraction" else "denoising",
        "-output",
        str(output_base),
        "-gpu",
        str(bool(values["gpu"])).lower(),
        "-ai_version",
        model_version,
    ]
    outputs = {"primary": output_base.with_suffix(".fits")}
    if stage == "background_extraction":
        correction = str(values["correction"])
        if correction not in {"Subtraction", "Division"}:
            raise GraXpertError("GraXpert correction must be Subtraction or Division")
        smoothing = float(values["smoothing"])
        if not 0 <= smoothing <= 1:
            raise GraXpertError("GraXpert smoothing must be between 0 and 1")
        command_line += ["-correction", correction, "-smoothing", f"{smoothing:g}"]
        if values.get("save_background", True):
            command_line.append("-bg")
            outputs["background_model"] = output_base.with_name(f"{output_base.name}_background.fits")
    else:
        strength = float(values["strength"])
        batch_size = int(values["batch_size"])
        if not 0 <= strength <= 1:
            raise GraXpertError("GraXpert denoise strength must be between 0 and 1")
        if batch_size not in {1, 2, 4, 8, 16, 32}:
            raise GraXpertError("GraXpert batch_size must be one of 1, 2, 4, 8, 16, 32")
        command_line += ["-strength", f"{strength:g}", "-batch_size", str(batch_size)]
    return command_line, outputs
