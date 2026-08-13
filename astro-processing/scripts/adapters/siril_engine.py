#!/usr/bin/env python3
"""Reproducible Siril CLI orchestration for OSC deep-sky processing.

Pixel operations are delegated to Siril and explicitly approved astronomy
tools.  This helper scans and classifies inputs, builds compatibility groups,
creates non-destructive staging links, discovers optional processors, renders
auditable scripts, tracks attempts, and performs guarded cleanup inside a
marked run directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SKILL_VERSION = "0.4.0"
MIN_SIRIL_VERSION = (1, 4, 0)
FITS_EXTENSIONS = {".fit", ".fits", ".fts"}
RAW_EXTENSIONS = {
    ".3fr", ".arw", ".cr2", ".cr3", ".dng", ".erf", ".kdc", ".mef",
    ".mos", ".mrw", ".nef", ".nrw", ".orf", ".pef", ".raf", ".raw",
    ".rw2", ".sr2", ".srf", ".x3f",
}
SUPPORTED_EXTENSIONS = FITS_EXTENSIONS | RAW_EXTENSIONS
FRAME_ALIASES = {
    "light": {"light", "lights", "object", "objects", "science"},
    "dark": {"dark", "darks"},
    "flat": {"flat", "flats"},
    "bias": {"bias", "biases", "offset", "offsets"},
    "darkflat": {"darkflat", "darkflats", "flatdark", "flatdarks"},
}
POST_STAGES = ("background", "color", "denoise", "stretch", "finish")


class PipelineError(RuntimeError):
    """An actionable pipeline error."""


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise PipelineError(f"Missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Invalid JSON in {path}: {exc}") from exc


def slug(value: str, fallback: str = "group") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:52] or fallback


def stable_id(parts: Iterable[Any], prefix: str = "g") -> str:
    serial = json.dumps(list(parts), ensure_ascii=True, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(serial.encode()).hexdigest()[:10]}"


def parse_fits_scalar(raw: str) -> Any:
    value = raw.strip()
    if value.startswith("'"):
        match = re.match(r"^'((?:''|[^'])*)'", value)
        return match.group(1).replace("''", "'").strip() if match else value.strip("'").strip()
    value = value.split("/", 1)[0].strip()
    if value == "T":
        return True
    if value == "F":
        return False
    if not value:
        return None
    try:
        if re.search(r"[.EeDd]", value):
            return float(value.replace("D", "E").replace("d", "e"))
        return int(value)
    except ValueError:
        return value


def read_fits_header(path: Path, max_blocks: int = 64) -> dict[str, Any]:
    header: dict[str, Any] = {}
    with path.open("rb") as handle:
        for _ in range(max_blocks):
            block = handle.read(2880)
            if not block:
                break
            if len(block) % 80:
                raise PipelineError(f"Malformed FITS header block in {path}")
            for offset in range(0, len(block), 80):
                card = block[offset:offset + 80].decode("ascii", errors="replace")
                key = card[:8].strip().upper()
                if key == "END":
                    return header
                if key and card[8:10] == "= ":
                    header[key] = parse_fits_scalar(card[10:])
    if not header:
        raise PipelineError(f"No FITS header found in {path}")
    return header


def norm_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def classify_frame(path: Path, header: dict[str, Any]) -> tuple[str, float, str]:
    image_type = norm_token(header.get("IMAGETYP") or header.get("FRAME") or header.get("TYPE"))
    for kind, aliases in FRAME_ALIASES.items():
        if image_type in {norm_token(item) for item in aliases}:
            return kind, 1.0, f"FITS IMAGETYP={header.get('IMAGETYP', image_type)!r}"

    path_tokens: list[str] = []
    for part in path.parts[-4:-1]:
        path_tokens.extend(re.findall(r"[a-z0-9]+", part.lower()))
    filename_tokens = re.findall(r"[a-z0-9]+", path.stem.lower())
    for source, tokens, confidence in (
        ("directory", path_tokens, 0.85),
        ("filename", filename_tokens[:4], 0.72),
    ):
        token_set = {norm_token(item) for item in tokens}
        for kind, aliases in FRAME_ALIASES.items():
            if token_set & {norm_token(item) for item in aliases}:
                return kind, confidence, f"{source} token"
    return "unknown", 0.0, "no reliable frame-type signal"


def first_value(header: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = header.get(key)
        if value not in (None, ""):
            return value
    return None


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def parse_rational(value: str) -> float | None:
    text = value.strip().split()[0] if value.strip() else ""
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)/([+-]?\d+(?:\.\d+)?)", text)
    if match:
        denominator = float(match.group(2))
        return float(match.group(1)) / denominator if denominator else None
    return as_float(text)


def read_raw_metadata(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Read common camera RAW EXIF fields with exiv2 when it is available."""
    executable = shutil.which("exiv2")
    if not executable:
        return {}, ["exiv2 is unavailable; RAW grouping relies on directory and filename evidence"]
    completed = subprocess.run(
        [executable, "-Pkt", str(path)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else f"exit {completed.returncode}"
        return {}, [f"exiv2 could not read RAW metadata: {message}"]
    tags: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        match = re.match(r"^(\S+)\s+\S+\s+(.*)$", line)
        if match:
            tags[match.group(1)] = match.group(2).strip()

    def tag(*names: str) -> str | None:
        for name in names:
            if tags.get(name):
                return tags[name]
        return None

    exposure_text = tag("Exif.Photo.ExposureTime", "Exif.Photo.ShutterSpeedValue")
    iso_text = tag("Exif.Photo.PhotographicSensitivity", "Exif.Photo.ISOSpeedRatings", "Exif.Image.ISOSpeedRatings")
    date_text = tag("Exif.Photo.DateTimeOriginal", "Exif.Photo.DateTimeDigitized", "Exif.Image.DateTime")
    date_obs = ""
    if date_text:
        match = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](.*)", date_text)
        date_obs = f"{match.group(1)}-{match.group(2)}-{match.group(3)}T{match.group(4)}" if match else date_text
    pseudo_header: dict[str, Any] = {
        "INSTRUME": tag("Exif.Image.Model", "Exif.Photo.CameraModelName") or "",
        "EXPTIME": parse_rational(exposure_text) if exposure_text else None,
        "ISO": int(float(iso_text.split()[0])) if iso_text and re.match(r"^\d+(?:\.\d+)?", iso_text) else None,
        "DATE-OBS": date_obs,
        "NAXIS1": as_float(tag("Exif.Photo.PixelXDimension", "Exif.Image.ImageWidth")),
        "NAXIS2": as_float(tag("Exif.Photo.PixelYDimension", "Exif.Image.ImageLength")),
    }
    return {key: value for key, value in pseudo_header.items() if value not in (None, "")}, []


def scan_file(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    header: dict[str, Any] = {}
    errors: list[str] = []
    if ext in FITS_EXTENSIONS:
        try:
            header = read_fits_header(path)
        except (OSError, PipelineError) as exc:
            errors.append(str(exc))
    elif ext in RAW_EXTENSIONS:
        try:
            header, raw_notes = read_raw_metadata(path)
        except (OSError, subprocess.SubprocessError) as exc:
            raw_notes = [f"RAW metadata inspection failed: {exc}"]
        # Missing optional RAW metadata is a warning, not a corrupt-file error.
        metadata_notes = raw_notes
    else:
        metadata_notes = []
    if ext in FITS_EXTENSIONS:
        metadata_notes = []
    kind, confidence, evidence = classify_frame(path, header)
    exposure = as_float(first_value(header, "EXPTIME", "EXPOSURE"))
    temperature = as_float(first_value(header, "CCD-TEMP", "CCD_TEMP", "SENSOR_T"))
    date_obs = str(first_value(header, "DATE-OBS", "DATEOBS") or "")
    result = {
        "path": str(path.resolve()),
        "relative_path": None,
        "extension": ext,
        "format": "fits" if ext in FITS_EXTENSIONS else "camera-raw",
        "bytes": path.stat().st_size,
        "kind": kind,
        "classification_confidence": confidence,
        "classification_evidence": evidence,
        "metadata": {
            "instrument": str(first_value(header, "INSTRUME", "CAMERA") or "").strip(),
            "object": str(first_value(header, "OBJECT") or "").strip(),
            "filter": str(first_value(header, "FILTER") or "").strip(),
            "width": first_value(header, "NAXIS1"),
            "height": first_value(header, "NAXIS2"),
            "bitpix": first_value(header, "BITPIX"),
            "xbin": first_value(header, "XBINNING", "CCDXBIN"),
            "ybin": first_value(header, "YBINNING", "CCDYBIN"),
            "gain": first_value(header, "GAIN", "ISO"),
            "offset": first_value(header, "OFFSET"),
            "bayer": str(first_value(header, "BAYERPAT", "BAYERPATTERN") or "").strip().upper(),
            "exposure": exposure,
            "temperature": temperature,
            "date_obs": date_obs,
            "session_date": date_obs[:10] if len(date_obs) >= 10 else "",
            "ra": as_float(first_value(header, "RA", "OBJCTRA")),
            "dec": as_float(first_value(header, "DEC", "OBJCTDEC")),
            "focal_length": as_float(first_value(header, "FOCALLEN", "FOCLEN")),
            "pixel_size": as_float(first_value(header, "XPIXSZ", "PIXSIZE")),
        },
        "errors": errors,
        "metadata_notes": metadata_notes,
    }
    return result


def scan_input(input_dir: Path) -> list[dict[str, Any]]:
    root = input_dir.expanduser().resolve()
    if not root.is_dir():
        raise PipelineError(f"Input directory does not exist: {root}")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        cursor = path.parent
        inside_marked_run = False
        while True:
            if (cursor / ".siril-run.json").is_file():
                inside_marked_run = True
                break
            if cursor == root or cursor.parent == cursor:
                break
            cursor = cursor.parent
        if inside_marked_run:
            continue
        record = scan_file(path)
        record["relative_path"] = str(path.relative_to(root))
        files.append(record)
    if not files:
        raise PipelineError(f"No supported FITS or camera RAW files found under {root}")
    return files


def group_key(record: dict[str, Any]) -> tuple[Any, ...]:
    meta = record["metadata"]
    target = meta["object"] or infer_target(Path(record["path"])) or "unknown-target"
    exposure = meta["exposure"]
    rounded_exposure = round(exposure, 6) if exposure is not None else None
    return (
        target,
        meta["session_date"] or "unknown-date",
        meta["instrument"] or "unknown-camera",
        meta["width"], meta["height"], meta["xbin"], meta["ybin"],
        meta["gain"], meta["offset"], meta["filter"] or "OSC", rounded_exposure,
    )


def infer_target(path: Path) -> str:
    match = re.search(r"(?i)(?:light|object)[-_ ]+(.+?)(?:[-_ ]+\d+(?:\.\d+)?s|[-_ ]+bin|$)", path.stem)
    return match.group(1).strip("-_ ") if match else ""


def compatible_base(light: dict[str, Any], calibration: dict[str, Any]) -> tuple[bool, list[str]]:
    lm, cm = light["metadata"], calibration["metadata"]
    reasons: list[str] = []
    for key in ("width", "height", "xbin", "ybin", "gain", "offset"):
        if lm.get(key) is not None and cm.get(key) is not None and lm[key] != cm[key]:
            reasons.append(f"{key} mismatch ({cm[key]} vs {lm[key]})")
    if lm.get("instrument") and cm.get("instrument") and norm_token(lm["instrument"]) != norm_token(cm["instrument"]):
        reasons.append(f"instrument mismatch ({cm['instrument']} vs {lm['instrument']})")
    if lm.get("bayer") and cm.get("bayer") and lm["bayer"] != cm["bayer"]:
        reasons.append(f"Bayer mismatch ({cm['bayer']} vs {lm['bayer']})")
    if calibration["kind"] in {"flat", "darkflat"} and lm.get("filter") and cm.get("filter"):
        if norm_token(lm["filter"]) != norm_token(cm["filter"]):
            reasons.append(f"filter mismatch ({cm['filter']} vs {lm['filter']})")
    return not reasons, reasons


def build_plan(input_dir: Path, files: list[dict[str, Any]], dark_temp_tolerance: float) -> dict[str, Any]:
    lights = [item for item in files if item["kind"] == "light" and not item["errors"]]
    if not lights:
        raise PipelineError("No reliably classified light frames were found")
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for light in lights:
        buckets.setdefault(group_key(light), []).append(light)

    calibrations = [item for item in files if item["kind"] in {"bias", "dark", "flat", "darkflat"} and not item["errors"]]
    groups: list[dict[str, Any]] = []
    plan_warnings: list[dict[str, Any]] = []
    for key, group_lights in sorted(buckets.items(), key=lambda pair: str(pair[0])):
        reference = group_lights[0]
        target = key[0]
        group_id = f"{slug(str(target))}-{stable_id(key, 'g').split('-', 1)[1]}"
        selected: dict[str, list[dict[str, Any]]] = {kind: [] for kind in ("bias", "dark", "flat", "darkflat")}
        rejected: list[dict[str, Any]] = []
        risky_darks: list[tuple[float, dict[str, Any]]] = []
        light_temp_values = [item["metadata"]["temperature"] for item in group_lights if item["metadata"]["temperature"] is not None]
        light_temp = sum(light_temp_values) / len(light_temp_values) if light_temp_values else None
        light_exp = reference["metadata"]["exposure"]
        for calibration in calibrations:
            ok, reasons = compatible_base(reference, calibration)
            if not ok:
                rejected.append({"path": calibration["path"], "kind": calibration["kind"], "reasons": reasons})
                continue
            kind = calibration["kind"]
            if kind == "dark":
                dark_exp = calibration["metadata"]["exposure"]
                if light_exp is not None and dark_exp is not None and abs(light_exp - dark_exp) > max(0.001, light_exp * 0.005):
                    rejected.append({"path": calibration["path"], "kind": kind, "reasons": [f"exposure mismatch ({dark_exp}s vs {light_exp}s)"]})
                    continue
                dark_temp = calibration["metadata"]["temperature"]
                delta = abs(dark_temp - light_temp) if dark_temp is not None and light_temp is not None else 0.0
                if delta > dark_temp_tolerance:
                    risky_darks.append((delta, calibration))
                    continue
            if kind == "darkflat":
                flat_exposures = [c["metadata"]["exposure"] for c in calibrations if c["kind"] == "flat" and c["metadata"]["exposure"] is not None]
                darkflat_exp = calibration["metadata"]["exposure"]
                if flat_exposures and darkflat_exp is not None and min(abs(darkflat_exp - value) for value in flat_exposures) > 0.002:
                    rejected.append({"path": calibration["path"], "kind": kind, "reasons": ["dark-flat exposure does not match a flat exposure"]})
                    continue
            selected[kind].append(calibration)

        warnings: list[dict[str, Any]] = []
        risky_paths: list[str] = []
        if not selected["dark"] and risky_darks:
            # Select a coherent temperature cluster, not only the single frame
            # whose sensor temperature happens to be closest to the lights.
            # A master dark needs a useful frame count, while mixing unrelated
            # dark-library temperatures would make optimization unreliable.
            with_temperature = [
                item for item in risky_darks
                if item[1]["metadata"].get("temperature") is not None
            ]
            cluster_half_width = max(0.5, min(1.0, dark_temp_tolerance / 2.0))
            clusters: list[list[tuple[float, dict[str, Any]]]] = []
            for _, candidate in with_temperature:
                center = candidate["metadata"]["temperature"]
                clusters.append([
                    item for item in with_temperature
                    if abs(item[1]["metadata"]["temperature"] - center) <= cluster_half_width
                ])
            if clusters:
                chosen_cluster = min(
                    clusters,
                    key=lambda cluster: (
                        -len(cluster),
                        sum(item[0] for item in cluster) / len(cluster),
                        max(item[1]["metadata"]["temperature"] for item in cluster)
                        - min(item[1]["metadata"]["temperature"] for item in cluster),
                    ),
                )
            else:
                chosen_cluster = risky_darks
            chosen_cluster = sorted(chosen_cluster, key=lambda item: item[1]["path"])
            risky_paths = [item[1]["path"] for item in chosen_cluster]
            mean_delta = sum(item[0] for item in chosen_cluster) / len(chosen_cluster)
            warnings.append({
                "code": "DARK_TEMPERATURE_MISMATCH",
                "severity": "high",
                "message": (
                    f"The best coherent dark group ({len(chosen_cluster)} frames) differs "
                    f"from lights by about {mean_delta:.1f} C; it is not selected automatically."
                ),
                "suggestion": "Prefer matching darks, compare an optimized-dark branch with a no-dark branch, or proceed without darks.",
            })
        if not selected["flat"]:
            warnings.append({"code": "NO_COMPATIBLE_FLAT", "severity": "high", "message": "No compatible flat frames were selected."})
        if not selected["bias"] and not selected["darkflat"]:
            warnings.append({"code": "NO_FLAT_CALIBRATOR", "severity": "medium", "message": "No compatible bias or dark-flat frames were selected for flat calibration."})
        if len(group_lights) < 5:
            warnings.append({"code": "TOO_FEW_LIGHTS", "severity": "high", "message": f"Only {len(group_lights)} light frames are present."})
        group = {
            "id": group_id,
            "target": target,
            "session_date": key[1],
            "signature": list(key),
            "lights": [item["path"] for item in group_lights],
            "calibrations": {kind: [item["path"] for item in values] for kind, values in selected.items()},
            "risky_dark_candidates": risky_paths,
            "reference_metadata": reference["metadata"],
            "light_temperature_mean": light_temp,
            "rejected_calibrations": rejected,
            "warnings": warnings,
        }
        groups.append(group)
        plan_warnings.extend({"group": group_id, **warning} for warning in warnings)

    unknown = [item for item in files if item["kind"] == "unknown" or item["errors"]]
    raw_low_confidence = [
        item for item in files
        if item["format"] == "camera-raw" and item["kind"] != "unknown" and item.get("metadata_notes")
    ]
    if unknown:
        plan_warnings.append({
            "group": None,
            "code": "QUARANTINED_FILES",
            "severity": "medium",
            "message": f"{len(unknown)} files could not be used reliably and remain quarantined.",
        })
    if raw_low_confidence:
        plan_warnings.append({
            "group": None,
            "code": "RAW_METADATA_INCOMPLETE",
            "severity": "medium",
            "message": f"{len(raw_low_confidence)} camera RAW files have incomplete EXIF metadata; verify exposure/camera grouping before processing.",
        })
    return {
        "schema_version": 1,
        "created_at": now_utc(),
        "skill_version": SKILL_VERSION,
        "input_dir": str(input_dir.resolve()),
        "dark_temperature_tolerance_c": dark_temp_tolerance,
        "counts": dict(Counter(item["kind"] for item in files)),
        "groups": groups,
        "quarantined": [{"path": item["path"], "errors": item["errors"], "evidence": item["classification_evidence"]} for item in unknown],
        "warnings": plan_warnings,
    }


def default_output(input_dir: Path) -> Path:
    root = input_dir.resolve()
    return root.parent / f"{root.name}-siril-output"


def make_run_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def create_run(input_dir: Path, output_dir: Path, run_id: str | None, dark_temp_tolerance: float) -> Path:
    files = scan_input(input_dir)
    plan = build_plan(input_dir.resolve(), files, dark_temp_tolerance)
    chosen_id = slug(run_id, make_run_id()) if run_id else make_run_id()
    run_dir = output_dir.expanduser().resolve() / chosen_id
    if run_dir.exists():
        raise PipelineError(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    marker = {
        "run_id": chosen_id,
        "created_at": now_utc(),
        "input_dir": str(input_dir.expanduser().resolve()),
        "run_dir": str(run_dir),
        "skill_version": SKILL_VERSION,
    }
    atomic_json(run_dir / ".siril-run.json", marker)
    atomic_json(run_dir / "manifest.json", {"schema_version": 1, "files": files})
    atomic_json(run_dir / "plan.json", plan)
    atomic_json(run_dir / "params.json", default_postprocess_params())
    state = {"schema_version": 1, "created_at": now_utc(), "updated_at": now_utc(), "groups": {}}
    for group in plan["groups"]:
        state["groups"][group["id"]] = {"preprocess": "pending", "attempts": [], "selected_attempt": None}
        base = run_dir / "groups" / group["id"]
        for name in ("inputs", "process", "masters", "checkpoints", "attempts", "external", "logs", "scripts"):
            (base / name).mkdir(parents=True, exist_ok=True)
    atomic_json(run_dir / "state.json", state)
    write_report(run_dir)
    return run_dir


def default_postprocess_params() -> dict[str, Any]:
    return {
        "style": "natural",
        "reference_notes": "Neutral background, protected highlights, restrained noise reduction and saturation.",
        "background": {"enabled": True, "method": "polynomial", "degree": 1, "samples": 20, "tolerance": 1.0, "dither": False, "smooth": 0.5},
        "color": {"enabled": True, "mode": "pcc", "catalog": "apass", "platesolve": True, "force_platesolve": False, "downscale": True, "osc_sensor": "", "osc_filter": "", "osc_lpf": "", "white_reference": ""},
        "denoise": {"enabled": True, "modulation": 0.45, "method": "base", "independent_channels": False},
        "stretch": {"method": "autostretch", "linked": True, "shadows_clip": -2.8, "amount": 3.0, "target_background": 0.15, "clip_mode": "rgbblend"},
        "finish": {"remove_green": False, "green_type": 0, "green_amount": 0.35, "saturation": 0.12, "saturation_background_factor": 1.0, "sharpen": False, "sharpen_sigma": 1.0, "sharpen_amount": 0.15, "jpeg_quality": 95},
        "iteration": {"max_attempts_per_stage": 15, "plateau_attempts": 3, "progress_report_minutes": 30},
    }


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _siril_preference_paths() -> list[Path]:
    home = Path.home()
    paths = [
        home / "Library/Application Support/org.free-astro.siril/siril/config.ini",
        home / ".config/siril/config.1.4.ini",
        home / ".config/siril/config.ini",
    ]
    return [path for path in paths if path.is_file()]


def _preference_value(key: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(key)}\s*=\s*(.*?)\s*$", re.IGNORECASE)
    for config in _siril_preference_paths():
        for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
            match = pattern.match(line)
            if match and match.group(1):
                return match.group(1)
    return None


def _first_executable(candidates: Iterable[str | Path | None]) -> Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if _executable(path):
            return path.resolve()
    return None


def discover_external_tools() -> dict[str, Any]:
    """Discover optional astronomy processors without installing anything."""
    home = Path.home()
    starnet = _first_executable([
        os.environ.get("STARNET_CLI"),
        shutil.which("starnet2"),
        shutil.which("starnet++"),
        shutil.which("starnet"),
        _preference_value("starnet_exe"),
        home / "StarNet2T_MacOS/starnet2",
    ])
    weight_candidates: list[str | Path | None] = [
        os.environ.get("STARNET_WEIGHTS"),
        _preference_value("starnet_weights"),
    ]
    if starnet:
        weight_candidates.append(starnet.parent / "StarNet2_weights.pt")
    weight_candidates.append(home / "StarNet2T_MacOS/StarNet2_weights.pt")
    starnet_weights = next(
        (Path(item).expanduser().resolve() for item in weight_candidates if item and Path(item).expanduser().is_file()),
        None,
    )
    graxpert = _first_executable([
        os.environ.get("GRAXPERT_CLI"),
        shutil.which("graxpert"),
        shutil.which("GraXpert"),
        _preference_value("graxpert_path"),
        Path.cwd() / "tools/GraXpert.app/Contents/MacOS/GraXpert",
        "/Applications/GraXpert.app/Contents/MacOS/GraXpert",
        home / "Applications/GraXpert.app/Contents/MacOS/GraXpert",
    ])
    rc_astro = _first_executable([
        os.environ.get("RC_ASTRO_CLI"),
        shutil.which("rc-astro"),
        "/usr/local/bin/rc-astro",
        "/opt/homebrew/bin/rc-astro",
    ])
    return {
        "starnet": {
            "available": bool(starnet and starnet_weights),
            "executable": str(starnet) if starnet else None,
            "weights": str(starnet_weights) if starnet_weights else None,
        },
        "graxpert": {
            "available": bool(graxpert),
            "executable": str(graxpert) if graxpert else None,
        },
        "rc_astro": {
            "available": bool(rc_astro),
            "executable": str(rc_astro) if rc_astro else None,
            "note": "BXT/NXT/SXT also require activated product licenses; discovery does not imply activation.",
        },
    }


def find_siril(explicit: str | None = None) -> Path:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get("SIRIL_CLI")
    if env_path:
        candidates.append(env_path)
    located = shutil.which("siril-cli")
    if located:
        candidates.append(located)
    if sys.platform == "darwin":
        candidates.extend([
            "/Applications/Siril.app/Contents/MacOS/siril-cli",
            str(Path.home() / "Applications/Siril.app/Contents/MacOS/siril-cli"),
        ])
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    raise PipelineError("siril-cli was not found. Install Siril only after user confirmation, then set SIRIL_CLI if needed.")


def siril_version(executable: Path) -> tuple[tuple[int, int, int], str]:
    completed = subprocess.run([str(executable), "--version"], text=True, capture_output=True, timeout=30, check=False)
    text = (completed.stdout + "\n" + completed.stderr).strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        raise PipelineError(f"Could not parse Siril version from: {text}")
    return tuple(int(part) for part in match.groups()), text


def require_run(run_dir: Path) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    resolved = run_dir.expanduser().resolve()
    marker = load_json(resolved / ".siril-run.json")
    if Path(marker.get("run_dir", "")).resolve() != resolved:
        raise PipelineError(f"Run marker does not match directory: {resolved}")
    return resolved, marker, load_json(resolved / "plan.json"), load_json(resolved / "state.json")


def group_by_id(plan: dict[str, Any], group_id: str) -> dict[str, Any]:
    for group in plan["groups"]:
        if group["id"] == group_id:
            return group
    raise PipelineError(f"Unknown group id: {group_id}")


def selected_groups(plan: dict[str, Any], requested: list[str] | None) -> list[dict[str, Any]]:
    if not requested:
        return plan["groups"]
    return [group_by_id(plan, group_id) for group_id in requested]


def safe_remove_contents(directory: Path) -> None:
    if not directory.exists():
        return
    for child in directory.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def stage_links(group_root: Path, group: dict[str, Any], allow_risky_dark: bool) -> dict[str, int]:
    inputs = group_root / "inputs"
    mapping: dict[str, list[str]] = {
        "lights": group["lights"],
        "biases": group["calibrations"]["bias"],
        "flats": group["calibrations"]["flat"],
        "darks": group["calibrations"]["dark"],
        "darkflats": group["calibrations"]["darkflat"],
    }
    if allow_risky_dark and not mapping["darks"]:
        mapping["darks"] = group.get("risky_dark_candidates", [])
    counts: dict[str, int] = {}
    for category, sources in mapping.items():
        target_dir = inputs / category
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_remove_contents(target_dir)
        prefix = category[:-1] if category.endswith("s") else category
        if category == "biases":
            prefix = "bias"
        for index, source in enumerate(sources, start=1):
            source_path = Path(source).resolve()
            extension = source_path.suffix.lower()
            target = target_dir / f"{prefix}_{index:05d}{extension}"
            try:
                target.symlink_to(source_path)
            except OSError as exc:
                raise PipelineError(f"Could not create staging symlink {target} -> {source_path}: {exc}") from exc
        counts[category] = len(sources)
    return counts


def ssf_quote(value: str | Path) -> str:
    text = str(value).replace("\\", "/")
    if "\n" in text or "\r" in text or '"' in text:
        raise PipelineError(f"Path or argument cannot be represented safely in Siril script: {text!r}")
    return f'"{text}"'


def ssf_option(name: str, value: str | Path) -> str:
    """Quote the entire Siril option, as required when its value has spaces."""
    if not re.fullmatch(r"-[a-zA-Z0-9_-]+", name):
        raise PipelineError(f"Unsafe Siril option name: {name}")
    text = str(value).replace("\\", "/")
    if "\n" in text or "\r" in text or '"' in text:
        raise PipelineError(f"Option value cannot be represented safely in Siril script: {text!r}")
    return f'"{name}={text}"'


def preprocess_script(group_root: Path, counts: dict[str, int], quality_k: float, allow_risky_dark: bool) -> str:
    if counts["lights"] < 2:
        raise PipelineError("At least two light frames are required for registration and stacking")
    lines = [
        "# Generated by astro-processing; review before running.",
        "requires 1.4.0",
    ]
    masters = group_root / "masters"
    checkpoints = group_root / "checkpoints"

    if counts["biases"]:
        lines += [
            f"cd {ssf_quote(group_root / 'inputs' / 'biases')}",
            f"convert bias {ssf_option('-out', group_root / 'process')}",
            f"cd {ssf_quote(group_root / 'process')}",
            f"stack bias rej w 3 3 -nonorm -32b {ssf_option('-out', masters / 'master-bias')}",
        ]

    if counts["darkflats"]:
        lines += [
            f"cd {ssf_quote(group_root / 'inputs' / 'darkflats')}",
            f"convert darkflat {ssf_option('-out', group_root / 'process')}",
            f"cd {ssf_quote(group_root / 'process')}",
            f"stack darkflat rej w 3 3 -nonorm -32b {ssf_option('-out', masters / 'master-darkflat')}",
        ]

    if counts["flats"]:
        lines += [
            f"cd {ssf_quote(group_root / 'inputs' / 'flats')}",
            f"convert flat {ssf_option('-out', group_root / 'process')}",
            f"cd {ssf_quote(group_root / 'process')}",
        ]
        if counts["darkflats"]:
            lines.append(f"calibrate flat {ssf_option('-dark', masters / 'master-darkflat.fit')} -cfa")
            flat_sequence = "pp_flat"
        elif counts["biases"]:
            lines.append(f"calibrate flat {ssf_option('-bias', masters / 'master-bias.fit')} -cfa")
            flat_sequence = "pp_flat"
        else:
            flat_sequence = "flat"
        lines.append(f"stack {flat_sequence} rej w 3 3 -norm=mul -32b {ssf_option('-out', masters / 'master-flat')}")

    if counts["darks"]:
        lines += [
            f"cd {ssf_quote(group_root / 'inputs' / 'darks')}",
            f"convert dark {ssf_option('-out', group_root / 'process')}",
            f"cd {ssf_quote(group_root / 'process')}",
            f"stack dark rej w 3 3 -nonorm -32b {ssf_option('-out', masters / 'master-dark')}",
        ]

    lines += [
        f"cd {ssf_quote(group_root / 'inputs' / 'lights')}",
        f"convert light {ssf_option('-out', group_root / 'process')}",
        f"cd {ssf_quote(group_root / 'process')}",
    ]
    calibration_args: list[str] = []
    if counts["darks"]:
        calibration_args += [ssf_option("-dark", masters / "master-dark.fit"), "-cc=dark"]
        if allow_risky_dark and counts["biases"]:
            calibration_args += [ssf_option("-bias", masters / "master-bias.fit"), "-opt"]
    elif counts["biases"]:
        calibration_args.append(ssf_option("-bias", masters / "master-bias.fit"))
    if counts["flats"]:
        calibration_args.append(ssf_option("-flat", masters / "master-flat.fit"))
        calibration_args.append("-equalize_cfa")
    calibration_args += ["-cfa", "-debayer"]
    lines += [
        "calibrate light " + " ".join(calibration_args),
        "register pp_light -2pass -selected",
        "seqapplyreg pp_light -framing=min -interp=lanczos4",
        (
            "stack r_pp_light rej w 3 3 -norm=addscale -output_norm -weight=wfwhm -32b "
            f"-filter-wfwhm={quality_k:g}k -filter-round={quality_k:g}k "
            f"-filter-bkg={quality_k:g}k -filter-nbstars={quality_k:g}k "
            f"{ssf_option('-out', checkpoints / 'stacked-linear')}"
        ),
        f"load {ssf_quote(checkpoints / 'stacked-linear.fit')}",
        "mirrorx -bottomup",
        f"save {ssf_quote(checkpoints / 'stacked-linear')}",
        f"jsonmetadata {ssf_quote(checkpoints / 'stacked-linear.fit')} -stats_from_loaded {ssf_option('-out', checkpoints / 'stacked-linear.json')}",
        "autostretch -linked -2.8 0.18",
        "resample -maxdim=2048 -interp=area",
        f"savepng {ssf_quote(checkpoints / 'stacked-linear-preview')}",
        "close",
    ]
    return "\n".join(lines) + "\n"


def estimate_disk_bytes(group: dict[str, Any]) -> int:
    source_bytes = sum(Path(path).stat().st_size for path in group["lights"])
    meta = group["reference_metadata"]
    width, height = meta.get("width"), meta.get("height")
    if width and height:
        # Two 32-bit RGB sequences (calibrated and registered), plus masters and safety margin.
        expanded = int(width) * int(height) * 3 * 4 * len(group["lights"]) * 2
        return int(expanded * 1.3 + source_bytes * 0.25)
    return int(source_bytes * 7.5)


def run_siril(executable: Path, script: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep Siril's config, cache, and optional Python environment inside the
    # marked run tree.  This avoids hidden state in the user's profile and
    # makes headless execution work in restricted environments.
    runtime_root = next((parent for parent in script.parents if (parent / ".siril-run.json").is_file()), None)
    environment = os.environ.copy()
    if runtime_root:
        config_home = runtime_root / "runtime" / "config"
        data_home = runtime_root / "runtime" / "data"
        cache_home = runtime_root / "runtime" / "cache"
        for directory in (config_home, data_home, cache_home):
            directory.mkdir(parents=True, exist_ok=True)
        environment.update({
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_DATA_HOME": str(data_home),
            "XDG_CACHE_HOME": str(cache_home),
        })
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            [str(executable), "-s", str(script)],
            cwd=script.parent.parent,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return completed.returncode


def parse_preprocess_log(log_path: Path, light_count: int) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    registration = re.findall(r"Total:\s*(\d+) failed,\s*(\d+) registered", text)
    stacked = re.findall(r"Rejection stacking complete\.\s*(\d+) images have been stacked", text)
    failed_registration = int(registration[-1][0]) if registration else None
    registered = int(registration[-1][1]) if registration else None
    stacked_lights = int(stacked[-1]) if stacked else None
    excluded = light_count - stacked_lights if stacked_lights is not None else None
    fraction = excluded / light_count if excluded is not None and light_count else None
    return {
        "input_lights": light_count,
        "registration_failed": failed_registration,
        "registered": registered,
        "stacked_lights": stacked_lights,
        "excluded_lights": excluded,
        "excluded_fraction": fraction,
        "review_required": fraction is None or fraction > 0.30,
    }


def update_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_utc()
    atomic_json(run_dir / "state.json", state)


def run_preprocess(
    run_dir: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
    requested_groups: list[str] | None,
    siril: str | None,
    quality_k: float,
    allow_risky_dark: bool,
    dry_run: bool,
    force: bool,
) -> None:
    executable: Path | None = None
    if not dry_run:
        executable = find_siril(siril)
        version, version_text = siril_version(executable)
        if version < MIN_SIRIL_VERSION:
            raise PipelineError(f"Siril >= {'.'.join(map(str, MIN_SIRIL_VERSION))} is required; found {version_text}")
    for group in selected_groups(plan, requested_groups):
        group_id = group["id"]
        group_root = run_dir / "groups" / group_id
        linear = group_root / "checkpoints" / "stacked-linear.fit"
        if linear.exists() and not force:
            print(f"[{group_id}] stacked linear checkpoint already exists; skipping")
            continue
        counts = stage_links(group_root, group, allow_risky_dark)
        script_text = preprocess_script(group_root, counts, quality_k, allow_risky_dark)
        script_path = group_root / "scripts" / "preprocess.ssf"
        script_path.write_text(script_text, encoding="utf-8")
        estimated = estimate_disk_bytes(group)
        free = shutil.disk_usage(run_dir).free
        state["groups"][group_id]["preflight"] = {
            "checked_at": now_utc(),
            "estimated_bytes": estimated,
            "free_bytes": free,
            "staged_counts": counts,
            "allow_risky_dark": allow_risky_dark,
            "quality_k": quality_k,
        }
        update_state(run_dir, state)
        print(f"[{group_id}] script: {script_path}")
        reserve = max(5 * 2**30, int(estimated * 0.1))
        print(f"[{group_id}] estimated working space {estimated / 2**30:.1f} GiB + {reserve / 2**30:.1f} GiB reserve; free {free / 2**30:.1f} GiB")
        if dry_run:
            continue
        if free < estimated + reserve:
            state["groups"][group_id]["preprocess"] = "blocked-insufficient-disk"
            update_state(run_dir, state)
            raise PipelineError(f"Insufficient free space for {group_id}: need about {(estimated + reserve) / 2**30:.1f} GiB including reserve, have {free / 2**30:.1f} GiB")
        state["groups"][group_id]["preprocess"] = "running"
        update_state(run_dir, state)
        log = group_root / "logs" / "preprocess.log"
        returncode = run_siril(executable, script_path, log)  # type: ignore[arg-type]
        if returncode != 0 or not linear.exists():
            state["groups"][group_id]["preprocess"] = "failed"
            state["groups"][group_id]["preprocess_returncode"] = returncode
            update_state(run_dir, state)
            raise PipelineError(f"Siril preprocessing failed for {group_id}; inspect {log}")
        metrics = parse_preprocess_log(log, len(group["lights"]))
        state["groups"][group_id]["preprocess_metrics"] = metrics
        state["groups"][group_id]["preprocess"] = "review-required" if metrics["review_required"] else "complete"
        state["groups"][group_id]["linear_checkpoint"] = str(linear)
        update_state(run_dir, state)
        if metrics["review_required"]:
            print(f"[{group_id}] review required: stacked {metrics['stacked_lights']} of {metrics['input_lights']} lights")
    write_report(run_dir)


def parse_frame_spec(spec: str | None, maximum: int) -> set[int]:
    if not spec or spec.strip().lower() == "all":
        return set(range(1, maximum + 1))
    selected: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise PipelineError(f"Invalid frame selection token: {token!r}")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first > last or first < 1 or last > maximum:
            raise PipelineError(f"Frame selection {token!r} is outside 1-{maximum}")
        selected.update(range(first, last + 1))
    if len(selected) < 3:
        raise PipelineError("Comet stacking requires at least three selected frames")
    return selected


def fits_observation_time(path: Path) -> dt.datetime:
    value = read_fits_header(path).get("DATE-OBS")
    if not value:
        raise PipelineError(f"Comet alignment requires DATE-OBS: {path}")
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise PipelineError(f"Invalid DATE-OBS {value!r} in {path}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def registered_sequence_variant(
    source_seq: Path,
    output_name: str,
    selected: set[int],
    reference_frame: int,
    source_images: list[Path],
    timestamps: list[dt.datetime],
    velocity_x: float = 0.0,
    velocity_y_display: float = 0.0,
) -> Path:
    """Create a Siril sequence variant using the same time-shift model as comet.c.

    Siril's GUI reports vertical velocity with the opposite sign to its internal
    bottom-up image coordinate. This interface accepts the intuitive preview
    convention (positive Y is down), then left-composes the matching shifts.
    """
    lines = source_seq.read_text(encoding="utf-8").splitlines()
    sequence_line = next((line for line in lines if line.startswith("S ")), None)
    if sequence_line is None:
        raise PipelineError(f"Missing S record in Siril sequence: {source_seq}")
    sequence_tokens = shlex.split(sequence_line)
    if len(sequence_tokens) < 8:
        raise PipelineError(f"Malformed S record in Siril sequence: {source_seq}")
    number = int(sequence_tokens[3])
    fixed = int(sequence_tokens[5])
    if number != len(source_images) or number != len(timestamps):
        raise PipelineError("Sequence length does not match the planned light-frame list")
    if reference_frame < 1 or reference_frame > number:
        raise PipelineError(f"Reference frame must be in 1-{number}")
    if reference_frame not in selected:
        raise PipelineError("Reference frame must be included in the comet stack")

    destination = source_seq.parent / f"{output_name}.seq"
    for frame_number, source_image in enumerate(source_images, start=1):
        if not source_image.is_file():
            raise PipelineError(f"Registered source frame is missing: {source_image}")
        target = source_seq.parent / f"{output_name}{frame_number:0{fixed}d}{source_image.suffix}"
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            raise PipelineError(f"Refusing to replace non-symlink comet staging file: {target}")
        target.symlink_to(source_image)

    reference_time = timestamps[reference_frame - 1]
    output: list[str] = []
    registration_index = 0
    for line in lines:
        if line.startswith("S "):
            tokens = shlex.split(line)
            tokens[1] = output_name
            tokens[4] = str(len(selected))
            tokens[6] = str(reference_frame - 1)
            output.append("S " + " ".join([f"'{tokens[1]}'", *tokens[2:]]))
        elif line.startswith("I "):
            tokens = line.split()
            frame_number = int(tokens[1])
            tokens[2] = "1" if frame_number in selected else "0"
            output.append(" ".join(tokens))
        elif line.startswith("R1 "):
            tokens = line.split()
            if len(tokens) < 17 or tokens[7] != "H":
                raise PipelineError(f"Malformed R1 homography in {source_seq}")
            delta_hours = (timestamps[registration_index] - reference_time).total_seconds() / 3600.0
            shift_x = -delta_hours * velocity_x
            # Applied sequence homographies use the opposite vertical sign
            # from Siril's exported preview coordinates. A down-moving object
            # therefore needs the negated time-relative Y displacement.
            shift_y_internal = -delta_hours * velocity_y_display
            matrix = [1.0, 0.0, shift_x, 0.0, 1.0, shift_y_internal, 0.0, 0.0, 1.0]
            output.append(" ".join([*tokens[:8], *(f"{value:.12g}" for value in matrix)]))
            registration_index += 1
        else:
            output.append(line)
    if registration_index != number:
        raise PipelineError(f"Expected {number} R1 records in {source_seq}, found {registration_index}")
    destination.write_text("\n".join(output) + "\n", encoding="utf-8")
    return destination


def comet_calibration_script(group_root: Path, allow_risky_dark: bool) -> str:
    masters = group_root / "masters"
    dark = masters / "master-dark.fit"
    bias = masters / "master-bias.fit"
    flat = masters / "master-flat.fit"
    if not dark.is_file():
        raise PipelineError("Comet workflow requires the retained master-dark.fit")
    args = [ssf_option("-dark", dark), "-cc=dark"]
    if allow_risky_dark:
        if not bias.is_file():
            raise PipelineError("Risky dark optimization requires the retained master-bias.fit")
        args += [ssf_option("-bias", bias), "-opt"]
    if flat.is_file():
        args += [ssf_option("-flat", flat), "-equalize_cfa"]
    args += ["-cfa", "-debayer"]
    return "\n".join([
        "# Recreate dark-calibrated registered lights for moving-object processing.",
        "requires 1.4.0",
        f"cd {ssf_quote(group_root / 'inputs' / 'lights')}",
        f"convert light {ssf_option('-out', group_root / 'process')}",
        f"cd {ssf_quote(group_root / 'process')}",
        "calibrate light " + " ".join(args),
        "register pp_light -2pass -selected",
        "seqapplyreg pp_light -framing=current -interp=lanczos4",
        "close",
        "",
    ])


def prune_precomet_calibration(process: Path) -> int:
    """Remove only reproducible light/pp_light data after r_pp_light is verified."""
    registered = sorted(process.glob("r_pp_light_*.fit"))
    if not (process / "r_pp_light_.seq").is_file() or len(registered) < 3:
        raise PipelineError("Refusing intermediate pruning: registered sequence is incomplete")
    reclaimed = 0
    for path in process.iterdir():
        if path.name in {"light_.seq", "pp_light_.seq"} or re.fullmatch(r"(?:pp_)?light_\d{5}\.fit", path.name):
            if path.is_file() and not path.is_symlink():
                reclaimed += path.stat().st_size
            path.unlink()
    return reclaimed


def comet_stack_script(
    group_root: Path,
    object_x: float,
    object_y: float,
    mask_width: int,
    mask_height: int,
    mask_blur: float,
    image_width: int,
    image_height: int,
    border_crop: int,
) -> str:
    process = group_root / "process"
    output = group_root / "comet"
    output.mkdir(parents=True, exist_ok=True)
    common = "rej w 3 3 -norm=addscale -output_norm -weight=wfwhm -32b"
    lines = [
        "# Dual registration: reject the moving comet in the star stack and moving stars in the comet stack.",
        "requires 1.4.0",
        f"cd {ssf_quote(process)}",
        f"stack star_r_pp_light {common} -filter-included {ssf_option('-out', output / 'star_aligned_linear')}",
        "seqapplyreg comet_r_pp_light -framing=current -interp=lanczos4 -filter-included -prefix=c_",
        f"stack c_comet_r_pp_light {common} {ssf_option('-out', output / 'comet_aligned_linear')}",
    ]
    for name in ("star_aligned_linear", "comet_aligned_linear"):
        lines += [
            f"load {ssf_quote(output / f'{name}.fit')}",
            "mirrorx -bottomup",
            f"save {ssf_quote(output / name)}",
        ]
    lines += [
        f"cd {ssf_quote(output)}",
        "load comet_aligned_linear.fit",
        "linear_match star_aligned_linear.fit 0.001 0.90",
        "save comet_aligned_matched",
        "load star_aligned_linear.fit",
        "fill 0",
        (
            f"fill 65535 {round(object_x - mask_width / 2)} {round(object_y - mask_height / 2)} "
            f"{mask_width} {mask_height}"
        ),
        f"gauss {mask_blur:g}",
        "save comet-mask",
        'pm "$star_aligned_linear$ * (1 - $comet-mask$) + $comet_aligned_matched$ * $comet-mask$" -nosum',
        *(
            [f"crop {border_crop} {border_crop} {image_width - 2 * border_crop} {image_height - 2 * border_crop}"]
            if border_crop else []
        ),
        f"save {ssf_quote(output / 'comet-linear')}",
        f"jsonmetadata {ssf_quote(output / 'comet-linear.fit')} -stats_from_loaded {ssf_option('-out', output / 'comet-linear.json')}",
        "autostretch -linked -2.8 0.18",
        "resample -maxdim=2048 -interp=area",
        f"savepng {ssf_quote(output / 'comet-linear-preview')}",
        "close",
        "",
    ]
    return "\n".join(lines)


def run_comet(
    run_dir: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
    group_id: str,
    siril: str | None,
    velocity_x: float,
    velocity_y: float,
    reference_frame: int,
    object_x: float,
    object_y: float,
    mask_width: int,
    mask_height: int,
    mask_blur: float,
    border_crop: int,
    include_spec: str | None,
    allow_risky_dark: bool,
    dry_run: bool,
    force: bool,
) -> None:
    group = group_by_id(plan, group_id)
    group_root = run_dir / "groups" / group_id
    process = group_root / "process"
    process.mkdir(parents=True, exist_ok=True)
    selected = parse_frame_spec(include_spec, len(group["lights"]))
    width = int(group["reference_metadata"].get("width") or 0)
    height = int(group["reference_metadata"].get("height") or 0)
    if not (0 <= object_x < width and 0 <= object_y < height):
        raise PipelineError(f"Object position must be inside the {width}x{height} registered frame")
    if min(mask_width, mask_height) < 32 or mask_blur <= 0:
        raise PipelineError("Comet mask dimensions must be at least 32 pixels and blur must be positive")
    if border_crop < 0 or 2 * border_crop >= min(width, height):
        raise PipelineError("Comet border crop is outside the registered frame")
    timestamps = [fits_observation_time(Path(path)) for path in group["lights"]]
    stage_links(group_root, group, allow_risky_dark)
    first_script = group_root / "scripts" / "comet-calibrate-register.ssf"
    first_script.write_text(comet_calibration_script(group_root, allow_risky_dark), encoding="utf-8")
    rseq = process / "r_pp_light_.seq"
    if force and process.exists():
        safe_remove_contents(process)
    elif any(process.iterdir()) and not rseq.is_file():
        raise PipelineError("Process directory is not empty and has no resumable r_pp_light sequence; use --force only after review")
    if dry_run:
        print(first_script)
        print(f"Selected {len(selected)} frames; reference={reference_frame}; velocity=({velocity_x:.3f}, {velocity_y:.3f}) preview px/hour")
        return
    executable = find_siril(siril)
    version, version_text = siril_version(executable)
    if version < MIN_SIRIL_VERSION:
        raise PipelineError(f"Siril >= 1.4.0 is required; found {version_text}")
    if not rseq.is_file():
        estimated = estimate_disk_bytes(group)
        free = shutil.disk_usage(run_dir).free
        if free < estimated:
            raise PipelineError(f"Insufficient free space for comet calibration: need {human_bytes(estimated)}, have {human_bytes(free)}")
        state["groups"][group_id]["comet"] = {"status": "calibrating", "started_at": now_utc()}
        update_state(run_dir, state)
        code = run_siril(executable, first_script, group_root / "logs" / "comet-calibrate-register.log")
        if code != 0 or not rseq.is_file():
            state["groups"][group_id]["comet"]["status"] = "failed-calibration"
            update_state(run_dir, state)
            raise PipelineError("Siril comet calibration/registration failed; inspect comet-calibrate-register.log")
    reclaimed = prune_precomet_calibration(process)
    source_images = [process / f"r_pp_light_{index:05d}.fit" for index in range(1, len(group["lights"]) + 1)]
    star_seq = registered_sequence_variant(
        rseq, "star_r_pp_light_", selected, reference_frame, source_images, timestamps
    )
    comet_seq = registered_sequence_variant(
        rseq, "comet_r_pp_light_", selected, reference_frame, source_images, timestamps,
        velocity_x, velocity_y,
    )
    second_script = group_root / "scripts" / "comet-dual-stack.ssf"
    second_script.write_text(
        comet_stack_script(
            group_root, object_x, object_y, mask_width, mask_height, mask_blur,
            width, height, border_crop,
        ),
        encoding="utf-8",
    )
    record = {
        "status": "stacking",
        "started_at": state["groups"][group_id].get("comet", {}).get("started_at", now_utc()),
        "velocity_preview_px_per_hour": {"x": velocity_x, "y": velocity_y},
        "reference_frame": reference_frame,
        "object_preview_position": {"x": object_x, "y": object_y},
        "composite_mask": {"width": mask_width, "height": mask_height, "blur": mask_blur},
        "border_crop": border_crop,
        "selected_frames": sorted(selected),
        "star_sequence": str(star_seq),
        "comet_sequence": str(comet_seq),
        "intermediate_reclaimed_bytes": reclaimed,
    }
    state["groups"][group_id]["comet"] = record
    update_state(run_dir, state)
    code = run_siril(executable, second_script, group_root / "logs" / "comet-dual-stack.log")
    linear = group_root / "comet" / "comet-linear.fit"
    if code != 0 or not linear.is_file():
        record["status"] = "failed-stacking"
        update_state(run_dir, state)
        raise PipelineError("Siril dual comet stacking failed; inspect comet-dual-stack.log")
    group_state = state["groups"][group_id]
    group_state.setdefault("previous_linear_checkpoint", group_state.get("linear_checkpoint"))
    group_state["linear_checkpoint"] = str(linear)
    record.update({"status": "complete", "completed_at": now_utc(), "linear_checkpoint": str(linear)})
    update_state(run_dir, state)
    write_report(run_dir)
    print(linear)


def validate_params(params: dict[str, Any]) -> None:
    background = params.get("background", {})
    if background.get("method") not in {"polynomial", "rbf"}:
        raise PipelineError("background.method must be polynomial or rbf")
    if not 1 <= int(background.get("degree", 1)) <= 4:
        raise PipelineError("background.degree must be between 1 and 4")
    color_mode = params.get("color", {}).get("mode", "pcc")
    if color_mode not in {"none", "pcc", "spcc"}:
        raise PipelineError("color.mode must be none, pcc, or spcc")
    if color_mode == "spcc" and not params["color"].get("osc_sensor"):
        raise PipelineError("color.osc_sensor is required for SPCC; use an exact name from Siril spcc_list")
    if (
        params.get("color", {}).get("enabled", True)
        and color_mode in {"pcc", "spcc"}
        and params.get("finish", {}).get("remove_green")
        and not params.get("finish", {}).get("allow_remove_green_after_photometric_calibration", False)
    ):
        raise PipelineError(
            "finish.remove_green is unsafe after PCC/SPCC. Disable it, or set "
            "allow_remove_green_after_photometric_calibration only after visually verifying a residual cast."
        )
    denoise_mod = float(params.get("denoise", {}).get("modulation", 0.55))
    if not 0 <= denoise_mod <= 1:
        raise PipelineError("denoise.modulation must be between 0 and 1")
    stretch = params.get("stretch", {})
    if stretch.get("method") not in {"autoghs", "autostretch", "ght", "modasinh"}:
        raise PipelineError("stretch.method must be autoghs, autostretch, ght, or modasinh")


def append_checkpoint(lines: list[str], attempt_dir: Path, stage: str, preview_linear: bool = True) -> None:
    checkpoint = attempt_dir / f"{stage}.fit"
    lines += [
        f"save {ssf_quote(checkpoint.with_suffix(''))}",
        f"jsonmetadata {ssf_quote(checkpoint)} -stats_from_loaded {ssf_option('-out', attempt_dir / f'{stage}.json')}",
    ]
    if preview_linear:
        lines += [
            "autostretch -linked -2.8 0.18",
            "resample -maxdim=2048 -interp=area",
            f"savepng {ssf_quote(attempt_dir / f'{stage}-preview')}",
            f"load {ssf_quote(checkpoint)}",
        ]
    else:
        lines += [
            "resample -maxdim=2048 -interp=area",
            f"savepng {ssf_quote(attempt_dir / f'{stage}-preview')}",
            f"load {ssf_quote(checkpoint)}",
        ]


def postprocess_script(
    input_checkpoint: Path,
    attempt_dir: Path,
    params: dict[str, Any],
    start_stage: str,
    end_stage: str | None = None,
) -> str:
    validate_params(params)
    start_index = POST_STAGES.index(start_stage)
    end_index = POST_STAGES.index(end_stage) if end_stage else len(POST_STAGES) - 1
    if end_index < start_index:
        raise PipelineError(f"end stage {POST_STAGES[end_index]} precedes start stage {start_stage}")
    lines = [
        "# Generated post-processing attempt. Pixel operations are all Siril commands.",
        "requires 1.4.0",
        f"load {ssf_quote(input_checkpoint)}",
    ]
    for index, stage in enumerate(POST_STAGES[start_index:end_index + 1], start=start_index):
        if stage == "background":
            cfg = params["background"]
            if cfg.get("enabled", True):
                if cfg["method"] == "rbf":
                    command = f"subsky -rbf -samples={int(cfg['samples'])} -tolerance={float(cfg['tolerance']):g} -smooth={float(cfg.get('smooth', 0.5)):g}"
                else:
                    command = f"subsky {int(cfg['degree'])} -samples={int(cfg['samples'])} -tolerance={float(cfg['tolerance']):g}"
                if cfg.get("dither"):
                    command += " -dither"
                lines.append(command)
            append_checkpoint(lines, attempt_dir, stage, True)
        elif stage == "color":
            cfg = params["color"]
            if cfg.get("enabled", True) and cfg.get("mode") != "none":
                if cfg.get("platesolve", True):
                    command = "platesolve"
                    if cfg.get("force_platesolve"):
                        command += " -force"
                    if cfg.get("downscale", True):
                        command += " -downscale"
                    lines.append(command)
                if cfg["mode"] == "pcc":
                    lines.append(f"pcc -catalog={cfg.get('catalog', 'apass')}")
                else:
                    command = f"spcc {ssf_option('-oscsensor', cfg['osc_sensor'])}"
                    if cfg.get("osc_filter"):
                        command += f" {ssf_option('-oscfilter', cfg['osc_filter'])}"
                    if cfg.get("osc_lpf"):
                        command += f" {ssf_option('-osclpf', cfg['osc_lpf'])}"
                    if cfg.get("white_reference"):
                        command += f" {ssf_option('-whiteref', cfg['white_reference'])}"
                    lines.append(command)
            append_checkpoint(lines, attempt_dir, stage, True)
        elif stage == "denoise":
            cfg = params["denoise"]
            if cfg.get("enabled", True):
                command = f"denoise -mod={float(cfg['modulation']):g}"
                method = cfg.get("method", "base")
                if method == "da3d":
                    command += " -da3d"
                elif method == "sos":
                    command += f" -sos={int(cfg.get('sos_iterations', 2))} -rho={float(cfg.get('rho', 0.8)):g}"
                if cfg.get("independent_channels"):
                    command += " -indep"
                lines.append(command)
            append_checkpoint(lines, attempt_dir, stage, True)
        elif stage == "stretch":
            cfg = params["stretch"]
            linked = " -linked" if cfg.get("linked", True) else ""
            method = cfg["method"]
            if method == "autoghs":
                lines.append(f"autoghs{linked} {float(cfg['shadows_clip']):g} {float(cfg['amount']):g} -clipmode={cfg.get('clip_mode', 'rgbblend')}")
            elif method == "autostretch":
                lines.append(f"autostretch{linked} {float(cfg['shadows_clip']):g} {float(cfg['target_background']):g}")
            elif method == "ght":
                lines.append(f"ght -D={float(cfg['amount']):g} -SP={float(cfg.get('symmetry_point', 0.0)):g} -HP={float(cfg.get('highlight_protection', 1.0)):g} -clipmode={cfg.get('clip_mode', 'rgbblend')} -human")
            else:
                lines.append(f"modasinh -D={float(cfg['amount']):g} -SP={float(cfg.get('symmetry_point', 0.0)):g} -HP={float(cfg.get('highlight_protection', 1.0)):g} -clipmode={cfg.get('clip_mode', 'rgbblend')} -human")
            append_checkpoint(lines, attempt_dir, stage, False)
        elif stage == "finish":
            cfg = params["finish"]
            if cfg.get("remove_green"):
                lines.append(f"rmgreen {int(cfg.get('green_type', 0))} {float(cfg.get('green_amount', 0.35)):g}")
            saturation = float(cfg.get("saturation", 0))
            if saturation:
                lines.append(f"satu {saturation:g} {float(cfg.get('saturation_background_factor', 1.0)):g} 6")
            if cfg.get("sharpen"):
                lines.append(f"unsharp {float(cfg.get('sharpen_sigma', 1.0)):g} {float(cfg.get('sharpen_amount', 0.15)):g}")
            append_checkpoint(lines, attempt_dir, stage, False)
            lines += [
                f"savetif {ssf_quote(attempt_dir / 'final')} -deflate",
                f"savepng {ssf_quote(attempt_dir / 'final')}",
                f"savejpg {ssf_quote(attempt_dir / 'final')} {int(cfg.get('jpeg_quality', 95))}",
            ]
    lines.append("close")
    return "\n".join(lines) + "\n"


def next_attempt(group_state: dict[str, Any]) -> str:
    numbers = []
    for attempt in group_state.get("attempts", []):
        match = re.fullmatch(r"attempt-(\d+)", attempt.get("id", ""))
        if match:
            numbers.append(int(match.group(1)))
    return f"attempt-{(max(numbers, default=0) + 1):03d}"


def run_postprocess(
    run_dir: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
    requested_groups: list[str] | None,
    params_path: Path | None,
    siril: str | None,
    start_stage: str,
    end_stage: str | None,
    from_checkpoint: Path | None,
    dry_run: bool,
) -> None:
    params_source = params_path.resolve() if params_path else run_dir / "params.json"
    params = load_json(params_source)
    validate_params(params)
    executable = None if dry_run else find_siril(siril)
    if executable:
        version, text = siril_version(executable)
        if version < MIN_SIRIL_VERSION:
            raise PipelineError(f"Siril >= 1.4.0 is required; found {text}")
    final_stage = end_stage or POST_STAGES[-1]
    if POST_STAGES.index(final_stage) < POST_STAGES.index(start_stage):
        raise PipelineError(f"end stage {final_stage} precedes start stage {start_stage}")
    for group in selected_groups(plan, requested_groups):
        group_id = group["id"]
        group_root = run_dir / "groups" / group_id
        group_state = state["groups"][group_id]
        attempt_id = next_attempt(group_state)
        attempt_dir = group_root / "attempts" / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        if from_checkpoint:
            input_checkpoint = from_checkpoint.expanduser().resolve()
        elif start_stage == "background":
            input_checkpoint = Path(
                group_state.get("linear_checkpoint")
                or group_root / "checkpoints" / "stacked-linear.fit"
            )
        else:
            previous = POST_STAGES[POST_STAGES.index(start_stage) - 1]
            selected = group_state.get("selected_attempt")
            if not selected:
                raise PipelineError(f"Starting at {start_stage} requires --from-checkpoint or a selected prior attempt")
            input_checkpoint = group_root / "attempts" / selected / f"{previous}.fit"
        if not input_checkpoint.is_file() and not dry_run:
            raise PipelineError(f"Post-processing input checkpoint does not exist: {input_checkpoint}")
        script = postprocess_script(input_checkpoint, attempt_dir, params, start_stage, final_stage)
        script_path = attempt_dir / "postprocess.ssf"
        script_path.write_text(script, encoding="utf-8")
        snapshot = attempt_dir / "params.json"
        atomic_json(snapshot, params)
        attempt_record = {
            "id": attempt_id,
            "created_at": now_utc(),
            "status": "planned" if dry_run else "running",
            "start_stage": start_stage,
            "end_stage": final_stage,
            "input_checkpoint": str(input_checkpoint),
            "params": str(snapshot),
            "script": str(script_path),
        }
        group_state.setdefault("attempts", []).append(attempt_record)
        update_state(run_dir, state)
        print(f"[{group_id}] post-processing attempt: {attempt_dir}")
        if dry_run:
            continue
        log = attempt_dir / "postprocess.log"
        returncode = run_siril(executable, script_path, log)  # type: ignore[arg-type]
        log_text = log.read_text(encoding="utf-8", errors="replace")
        color_executed = (
            POST_STAGES.index(start_stage) <= POST_STAGES.index("color") <= POST_STAGES.index(final_stage)
        )
        requested_color = (
            color_executed
            and params.get("color", {}).get("enabled", True)
            and params.get("color", {}).get("mode") in {"pcc", "spcc"}
        )
        color_mode = params.get("color", {}).get("mode", "none")
        plate_solve_succeeded = "Siril solve succeeded." in log_text
        if not requested_color:
            color_calibration_succeeded = None
        elif color_mode == "pcc":
            color_calibration_succeeded = "Photometric Color Calibration succeeded." in log_text
        elif color_mode == "spcc":
            color_calibration_succeeded = "Spectrophotometric Color Calibration succeeded." in log_text
        else:
            color_calibration_succeeded = None
        attempt_record["online_calibration"] = {
            "requested": requested_color,
            "plate_solve_succeeded": plate_solve_succeeded if requested_color else None,
            "color_calibration_succeeded": color_calibration_succeeded,
            "imprecise_solution_warning": "imprecise solution" in log_text.lower(),
            "dns_or_catalog_failure": bool(re.search(r"libcurl error:\s*\[6\]|unable to retrieve the remote catalogue", log_text, re.IGNORECASE)),
        }
        expected = attempt_dir / f"{final_stage}.fit"
        attempt_record["returncode"] = returncode
        calibration_ok = not requested_color or (plate_solve_succeeded and bool(color_calibration_succeeded))
        attempt_record["status"] = "complete" if returncode == 0 and expected.exists() and calibration_ok else "failed"
        attempt_record["completed_at"] = now_utc()
        update_state(run_dir, state)
        if attempt_record["status"] != "complete":
            if requested_color and not calibration_ok:
                raise PipelineError(f"Online color calibration did not complete for {group_id}; inspect {log}")
            raise PipelineError(f"Post-processing attempt failed for {group_id}; inspect {log}")
    write_report(run_dir)


def copy_selected_outputs(run_dir: Path, plan: dict[str, Any], state: dict[str, Any], group_id: str, attempt_id: str) -> Path:
    group_by_id(plan, group_id)
    group_root = run_dir / "groups" / group_id
    attempt_dir = group_root / "attempts" / attempt_id
    attempt = next((item for item in state["groups"][group_id].get("attempts", []) if item["id"] == attempt_id), None)
    if not attempt or attempt.get("status") != "complete":
        raise PipelineError(f"Attempt is not complete: {group_id}/{attempt_id}")
    required = [attempt_dir / name for name in ("finish.fit", "final.tif", "final.png", "final.jpg")]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise PipelineError("Attempt cannot be selected because outputs are missing: " + ", ".join(map(str, missing)))
    final_dir = run_dir / "final" / group_id
    final_dir.mkdir(parents=True, exist_ok=True)
    linear_source = Path(
        state["groups"][group_id].get("linear_checkpoint")
        or group_root / "checkpoints" / "stacked-linear.fit"
    )
    source_map = {
        linear_source: final_dir / "stacked-linear.fit",
        attempt_dir / "finish.fit": final_dir / "final-processed.fit",
        attempt_dir / "final.tif": final_dir / "final.tif",
        attempt_dir / "final.png": final_dir / "final.png",
        attempt_dir / "final.jpg": final_dir / "final.jpg",
    }
    for source, target in source_map.items():
        if source.is_file():
            shutil.copy2(source, target)
    state["groups"][group_id]["selected_attempt"] = attempt_id
    state["groups"][group_id]["final_dir"] = str(final_dir)
    update_state(run_dir, state)
    write_report(run_dir)
    return final_dir


def human_bytes(value: int | float) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(amount) < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def write_report(run_dir: Path) -> Path:
    _, marker, plan, state = require_run(run_dir)
    lines = [
        "# Astrophotography processing report",
        "",
        f"- Run: `{marker['run_id']}`",
        f"- Input: `{marker['input_dir']}`",
        f"- Updated: `{state['updated_at']}`",
        "",
        "## Classification",
        "",
    ]
    for kind, count in sorted(plan["counts"].items()):
        lines.append(f"- {kind}: {count}")
    lines += ["", "## Groups", ""]
    for group in plan["groups"]:
        group_state = state["groups"][group["id"]]
        staged = group_state.get("preflight", {}).get("staged_counts", {})
        bias_count = staged.get("biases", len(group["calibrations"]["bias"]))
        dark_count = staged.get("darks", len(group["calibrations"]["dark"]))
        flat_count = staged.get("flats", len(group["calibrations"]["flat"]))
        darkflat_count = staged.get("darkflats", len(group["calibrations"]["darkflat"]))
        lines += [
            f"### {group['id']}",
            "",
            f"- Target: {group['target']}",
            f"- Session: {group['session_date']}",
            f"- Lights: {len(group['lights'])}",
            f"- Used bias / dark / flat / dark-flat: {bias_count} / {dark_count} / {flat_count} / {darkflat_count}",
            f"- Preprocess: {group_state.get('preprocess', 'pending')}",
            f"- Selected attempt: {group_state.get('selected_attempt') or 'none'}",
        ]
        if group_state.get("preflight", {}).get("allow_risky_dark"):
            lines.append("- Dark handling: temperature-mismatched dark group used with bias-assisted Siril dark optimization")
        metrics = group_state.get("preprocess_metrics")
        if metrics:
            fraction = metrics.get("excluded_fraction")
            fraction_text = f"{fraction:.1%}" if isinstance(fraction, (int, float)) else "unknown"
            lines.append(f"- Stacked lights: {metrics.get('stacked_lights')} / {metrics.get('input_lights')} (excluded {fraction_text})")
        if group_state.get("manual_qc"):
            lines.append(f"- Manual QC: {group_state['manual_qc']}")
        comet = group_state.get("comet")
        if comet:
            velocity = comet.get("velocity_preview_px_per_hour", {})
            position = comet.get("object_preview_position", {})
            mask = comet.get("composite_mask", {})
            lines.append(
                f"- Comet workflow: {comet.get('status', 'unknown')}; "
                f"velocity ({velocity.get('x', 'unknown')}, {velocity.get('y', 'unknown')}) preview px/hour; "
                f"reference frame {comet.get('reference_frame', 'unknown')}; "
                f"object ({position.get('x', 'unknown')}, {position.get('y', 'unknown')}); "
                f"mask {mask.get('width', 'unknown')}x{mask.get('height', 'unknown')} blur {mask.get('blur', 'unknown')}; "
                f"border crop {comet.get('border_crop', 'unknown')} px"
            )
        for warning in group.get("warnings", []):
            lines.append(f"- Warning [{warning['severity']} / {warning['code']}]: {warning['message']}")
        for attempt in group_state.get("attempts", []):
            lines.append(
                f"- Attempt `{attempt['id']}`: {attempt['status']} "
                f"({attempt['start_stage']} through {attempt.get('end_stage', POST_STAGES[-1])})"
            )
        for attempt in group_state.get("processor_attempts", []):
            review = attempt.get("visual_review", {})
            review_text = f"; visual review {review.get('verdict')}" if review else ""
            candidate_text = "; A/B candidate only" if attempt.get("candidate_only") else ""
            lines.append(
                f"- Processor attempt `{attempt['id']}`: {attempt.get('status', 'unknown')} "
                f"({attempt.get('stage')} via {attempt.get('processor')}{candidate_text}{review_text})"
            )
        if group_state.get("final_dir"):
            lines.append(f"- Final outputs: `{group_state['final_dir']}`")
        lines.append("")
    if plan.get("quarantined"):
        lines += ["## Quarantine", "", f"{len(plan['quarantined'])} files were not used because classification or parsing was unreliable.", ""]
    global_warnings = [warning for warning in plan.get("warnings", []) if warning.get("group") is None]
    if global_warnings:
        lines += ["## Global warnings", ""]
        for warning in global_warnings:
            lines.append(f"- [{warning['severity']} / {warning['code']}]: {warning['message']}")
        lines.append("")
    report = run_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def cleanup_candidates(run_dir: Path, plan: dict[str, Any], state: dict[str, Any], profile: str) -> list[Path]:
    candidates: list[Path] = []
    for group in plan["groups"]:
        group_id = group["id"]
        group_root = run_dir / "groups" / group_id
        group_state = state["groups"][group_id]
        if profile == "aborted":
            preprocess_state = str(group_state.get("preprocess", ""))
            if group_state.get("selected_attempt"):
                raise PipelineError(f"Refusing aborted cleanup: {group_id} already has a selected final attempt")
            if preprocess_state != "failed" and not preprocess_state.startswith("interrupted"):
                raise PipelineError(
                    f"Refusing aborted cleanup: {group_id} is {preprocess_state or 'unknown'}, not failed or interrupted"
                )
            candidates += [group_root / "inputs", group_root / "process"]
            continue
        if not group_state.get("selected_attempt"):
            raise PipelineError(f"Refusing cleanup: {group_id} has no selected final attempt")
        candidates += [group_root / "inputs", group_root / "process"]
        if profile == "minimal":
            selected = group_state["selected_attempt"]
            for attempt_dir in (group_root / "attempts").glob("attempt-*"):
                if attempt_dir.name != selected:
                    candidates.append(attempt_dir)
            candidates += [
                group_root / "masters",
                group_root / "checkpoints",
                group_root / "attempts" / selected,
                group_root / "external",
            ]
    return [path for path in candidates if path.exists()]


def cleanup_run(run_dir: Path, profile: str, confirm: str | None) -> None:
    resolved, marker, plan, state = require_run(run_dir)
    candidates = cleanup_candidates(resolved, plan, state, profile)
    total = sum(path.stat().st_size if path.is_file() else sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink()) for path in candidates)
    print(f"Cleanup profile: {profile}")
    print(f"Estimated reclaimable space: {human_bytes(total)}")
    for path in candidates:
        print(path)
    if confirm is None:
        print(f"Dry run only. Re-run with --confirm {marker['run_id']} after verifying this list.")
        return
    if confirm != marker["run_id"]:
        raise PipelineError("Cleanup confirmation does not match the run id")
    for path in candidates:
        candidate = path.resolve()
        if resolved not in candidate.parents:
            raise PipelineError(f"Refusing to delete path outside run directory: {candidate}")
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    state["cleanup"] = {"profile": profile, "completed_at": now_utc(), "reclaimed_estimate_bytes": total}
    update_state(resolved, state)
    write_report(resolved)


def print_inspection(input_dir: Path, files: list[dict[str, Any]], plan: dict[str, Any]) -> None:
    print(f"Input: {input_dir.resolve()}")
    print(f"Files: {len(files)} ({human_bytes(sum(item['bytes'] for item in files))})")
    print("Classification: " + ", ".join(f"{kind}={count}" for kind, count in sorted(plan["counts"].items())))
    print(f"Processing groups: {len(plan['groups'])}")
    for group in plan["groups"]:
        meta = group["reference_metadata"]
        print(
            f"  {group['id']}: {len(group['lights'])} lights, target={group['target']}, "
            f"camera={meta.get('instrument') or 'unknown'}, exposure={meta.get('exposure')}s"
        )
        for warning in group["warnings"]:
            print(f"    {warning['severity'].upper()} {warning['code']}: {warning['message']}")
    if plan["quarantined"]:
        print(f"Quarantined: {len(plan['quarantined'])}")
    for warning in plan.get("warnings", []):
        if warning.get("group") is None:
            print(f"{warning['severity'].upper()} {warning['code']}: {warning['message']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {SKILL_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_p = sub.add_parser("doctor", help="discover Siril and optional StarNet, GraXpert, and RC-Astro backends")
    doctor_p.add_argument("--json", type=Path, help="optional JSON destination")
    doctor_p.add_argument("--siril")

    inspect_p = sub.add_parser("inspect", help="scan and classify an input tree without writing to it")
    inspect_p.add_argument("--input", type=Path, required=True)
    inspect_p.add_argument("--json", type=Path, help="optional JSON destination")
    inspect_p.add_argument("--dark-temp-tolerance", type=float, default=5.0)

    plan_p = sub.add_parser("plan", help="create a resumable run directory, manifest, and processing plan")
    plan_p.add_argument("--input", type=Path, required=True)
    plan_p.add_argument("--output", type=Path)
    plan_p.add_argument("--run-id")
    plan_p.add_argument("--dark-temp-tolerance", type=float, default=5.0)

    preflight_p = sub.add_parser("preflight", help="check Siril version and estimated disk requirements")
    preflight_p.add_argument("--run", type=Path, required=True)
    preflight_p.add_argument("--siril")

    run_p = sub.add_parser("run", help="stage inputs and preprocess/register/stack with Siril")
    run_p.add_argument("--run", type=Path, required=True)
    run_p.add_argument("--group", action="append")
    run_p.add_argument("--siril")
    run_p.add_argument("--quality-k", type=float, default=3.0)
    run_p.add_argument("--allow-risky-dark", action="store_true", help="use closest mismatched darks with Siril dark optimization")
    run_p.add_argument("--dry-run", action="store_true", help="stage links and render scripts without invoking Siril")
    run_p.add_argument("--force", action="store_true")

    resume_p = sub.add_parser("resume", help="resume preprocessing, skipping completed linear checkpoints")
    for action in run_p._actions[1:]:  # mirror stable run options without duplicating behavior
        if action.dest == "force":
            continue
        if isinstance(action, argparse._StoreTrueAction):
            resume_p.add_argument(*action.option_strings, action="store_true", help=action.help)
        elif isinstance(action, argparse._AppendAction):
            resume_p.add_argument(*action.option_strings, action="append", help=action.help)
        else:
            resume_p.add_argument(*action.option_strings, type=action.type, required=action.required, default=action.default, help=action.help)

    post_p = sub.add_parser("postprocess", help="create and run a checkpointed visual-refinement attempt")
    post_p.add_argument("--run", type=Path, required=True)
    post_p.add_argument("--group", action="append")
    post_p.add_argument("--params", type=Path)
    post_p.add_argument("--siril")
    post_p.add_argument("--start-stage", choices=POST_STAGES, default="background")
    post_p.add_argument("--end-stage", choices=POST_STAGES, help="stop after this checkpoint instead of running through finish")
    post_p.add_argument("--from-checkpoint", type=Path)
    post_p.add_argument("--dry-run", action="store_true")

    comet_p = sub.add_parser("comet", help="create a dark-calibrated dual star/comet stack from measured motion")
    comet_p.add_argument("--run", type=Path, required=True)
    comet_p.add_argument("--group", required=True)
    comet_p.add_argument("--siril")
    comet_p.add_argument("--velocity-x", type=float, required=True, help="horizontal preview motion in pixels/hour; right is positive")
    comet_p.add_argument("--velocity-y", type=float, required=True, help="vertical preview motion in pixels/hour; down is positive")
    comet_p.add_argument("--reference-frame", type=int, required=True, help="one-based frame used as the moving-object position reference")
    comet_p.add_argument("--object-x", type=float, required=True, help="object X position in the registered reference preview")
    comet_p.add_argument("--object-y", type=float, required=True, help="object Y position in the registered reference preview")
    comet_p.add_argument("--mask-width", type=int, default=360, help="full-strength local comet layer width")
    comet_p.add_argument("--mask-height", type=int, default=650, help="full-strength local comet layer height")
    comet_p.add_argument("--mask-blur", type=float, default=90.0, help="Siril Gaussian feather radius")
    comet_p.add_argument("--border-crop", type=int, default=40, help="pixels trimmed per edge after recombination")
    comet_p.add_argument("--include", help="one-based comma/range selection, for example 1-21,23-28,30-32")
    comet_p.add_argument("--allow-risky-dark", action="store_true", help="use retained mismatched dark/bias masters with Siril dark optimization")
    comet_p.add_argument("--dry-run", action="store_true")
    comet_p.add_argument("--force", action="store_true", help="rebuild comet process intermediates after preserving outputs")

    select_p = sub.add_parser("select", help="select a completed attempt and materialize final exports")
    select_p.add_argument("--run", type=Path, required=True)
    select_p.add_argument("--group", required=True)
    select_p.add_argument("--attempt", required=True)

    report_p = sub.add_parser("report", help="regenerate and print the run report path")
    report_p.add_argument("--run", type=Path, required=True)

    clean_p = sub.add_parser("cleanup", help="preview or perform marker-guarded cleanup")
    clean_p.add_argument("--run", type=Path, required=True)
    clean_p.add_argument("--profile", choices=("keep-all", "standard", "minimal", "aborted"), default="standard")
    clean_p.add_argument("--confirm", help="exact run id; omit for dry-run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            tools = discover_external_tools()
            try:
                executable = find_siril(args.siril)
                version, version_text = siril_version(executable)
                tools["siril"] = {
                    "available": version >= MIN_SIRIL_VERSION,
                    "executable": str(executable),
                    "version": ".".join(map(str, version)),
                    "raw_version": version_text,
                }
            except PipelineError as exc:
                tools["siril"] = {"available": False, "error": str(exc)}
            print(json.dumps(tools, ensure_ascii=False, indent=2, sort_keys=True))
            if args.json:
                atomic_json(args.json.expanduser().resolve(), tools)
        elif args.command == "inspect":
            files = scan_input(args.input)
            plan = build_plan(args.input.resolve(), files, args.dark_temp_tolerance)
            print_inspection(args.input, files, plan)
            if args.json:
                atomic_json(args.json.expanduser().resolve(), {"files": files, "plan": plan})
        elif args.command == "plan":
            output = args.output or default_output(args.input)
            run_dir = create_run(args.input, output, args.run_id, args.dark_temp_tolerance)
            print(run_dir)
        elif args.command == "preflight":
            run_dir, _, plan, _ = require_run(args.run)
            executable = find_siril(args.siril)
            version, text = siril_version(executable)
            print(text)
            if version < MIN_SIRIL_VERSION:
                raise PipelineError("Siril >= 1.4.0 is required")
            free = shutil.disk_usage(run_dir).free
            print(f"Free space: {human_bytes(free)}")
            for group in plan["groups"]:
                estimated = estimate_disk_bytes(group)
                reserve = max(5 * 2**30, int(estimated * 0.1))
                verdict = "OK" if free >= estimated + reserve else "BLOCKED"
                print(f"{group['id']}: estimated {human_bytes(estimated)} + reserve {human_bytes(reserve)} [{verdict}]")
        elif args.command in {"run", "resume"}:
            run_dir, _, plan, state = require_run(args.run)
            run_preprocess(run_dir, plan, state, args.group, args.siril, args.quality_k, args.allow_risky_dark, args.dry_run, getattr(args, "force", False))
        elif args.command == "postprocess":
            run_dir, _, plan, state = require_run(args.run)
            run_postprocess(
                run_dir, plan, state, args.group, args.params, args.siril,
                args.start_stage, args.end_stage, args.from_checkpoint, args.dry_run,
            )
        elif args.command == "comet":
            run_dir, _, plan, state = require_run(args.run)
            run_comet(
                run_dir, plan, state, args.group, args.siril,
                args.velocity_x, args.velocity_y, args.reference_frame,
                args.object_x, args.object_y, args.mask_width, args.mask_height, args.mask_blur,
                args.border_crop,
                args.include, args.allow_risky_dark, args.dry_run, args.force,
            )
        elif args.command == "select":
            run_dir, _, plan, state = require_run(args.run)
            print(copy_selected_outputs(run_dir, plan, state, args.group, args.attempt))
        elif args.command == "report":
            print(write_report(args.run.expanduser().resolve()))
        elif args.command == "cleanup":
            if args.profile == "keep-all":
                print("keep-all selected; nothing will be removed")
            else:
                cleanup_run(args.run, args.profile, args.confirm)
        return 0
    except (PipelineError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
