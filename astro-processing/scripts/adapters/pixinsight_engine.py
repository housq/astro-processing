#!/usr/bin/env python3
"""Resumable PixInsight run generation and constrained GUI/PJSR execution."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from . import pixinsight


class PipelineError(RuntimeError):
    pass


PJSR_TEMPLATE = Path(__file__).resolve().parents[2] / "pjsr" / "pixinsight_pipeline.js"


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def default_output(input_path: Path) -> Path:
    return input_path.expanduser().resolve().parent / f"{input_path.stem}-pixinsight-output"


def inspect_declared_input(path: Path, input_state: str, data_type: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = path.expanduser().resolve()
    blockers: list[dict[str, str]] = []
    if not source.is_file():
        blockers.append({"code": "PIXINSIGHT_INPUT_NOT_FILE", "message": "PixInsight post-integration input must be one XISF file"})
    if source.suffix.lower() != ".xisf":
        blockers.append({"code": "PIXINSIGHT_INTERNAL_FORMAT", "message": "PixInsight internal checkpoints must use XISF"})
    if input_state != "integrated-linear":
        blockers.append({"code": "PIXINSIGHT_INPUT_STATE_REQUIRED", "message": "Declare --input-state integrated-linear; linearity is semantic and cannot be inferred safely from the filename"})
    if data_type != "osc-color":
        blockers.append({"code": "PIXINSIGHT_DATA_TYPE_UNVALIDATED", "message": "This adapter is validated only for post-integration OSC color data"})
    return {
        "path": str(source),
        "bytes": source.stat().st_size if source.is_file() else 0,
        "format": source.suffix.lower().lstrip("."),
        "declared_linear": input_state == "integrated-linear",
        "declared_data_type": data_type,
    }, blockers


def default_params(input_path: Path, run_dir: Path, rc_astro_enabled: bool, license_confirmed: bool) -> dict[str, Any]:
    checkpoints = run_dir / "checkpoints"
    exports = run_dir / "exports"
    logs = run_dir / "logs"
    return {
        "schema_version": 1,
        "mode": "pipeline",
        "input": str(input_path.expanduser().resolve()),
        "input_state": "integrated-linear",
        "data_type": "osc-color",
        "result": str(logs / "result.json"),
        "console_log": str(logs / "pixinsight-console.log"),
        "output": {"checkpoints": str(checkpoints), "exports": str(exports)},
        "stages": {
            "background": {"enabled": True, "method": "ABE", "degree": 1, "tolerance": 1.0, "deviation": 0.8, "box_size": 10, "box_separation": 10, "downsample": 2.0},
            "color": {"enabled": True, "method": "classic", "background_low": 0.0, "background_high": 0.10, "target_background": 0.001, "structure_layers": 5, "noise_layers": 1, "white_low": 0.0, "white_high": 0.90},
            "rc_astro": {
                "enabled": rc_astro_enabled,
                "license_confirmed": license_confirmed,
                "bxt": {"model": "BlurXTerminator.4.mlpackage", "correct_first": False, "sharpen_stars": 0.30, "sharpen_nonstellar": 0.35, "adjust_halos": 0.0},
                "sxt": {"model": "StarXTerminator.lite.nonoise.11.mlpackage", "overlap": 0.20, "unscreen": False},
                "nxt": {"model": "NoiseXTerminator.3.mlpackage", "denoise": 0.55, "denoise_color": 0.65, "iterations": 2, "detail": 0.20},
            },
            "native_denoise": {"enabled": not rc_astro_enabled, "method": "MLT"},
            "finish": {"enabled": True, "starless_midtones": 0.0045, "stars_midtones": 0.012, "single_midtones": 0.005254792191279071, "starless_saturation": 0.12, "stars_saturation": 0.04, "single_saturation": 0.08, "star_strength": 0.70},
            "exports": {"xisf": True, "fits32": True, "tiff": True, "png": True, "jpeg": True, "verify_reopen": True},
        },
        "limitations": {"spcc": "skipped", "image_solver": "not automated", "dbe": "not implemented", "wbpp": "not implemented"},
    }


def create_run(input_path: Path, output_dir: Path, run_id: str | None, rc_astro_enabled: bool, license_confirmed: bool) -> Path:
    chosen = run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_dir.expanduser().resolve() / chosen
    if run_dir.exists():
        raise PipelineError(f"Run directory already exists: {run_dir}")
    for name in ("checkpoints", "exports", "logs", "scripts"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    params = default_params(input_path, run_dir, rc_astro_enabled, license_confirmed)
    (run_dir / "params.json").write_text(json.dumps(params, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "state.json").write_text(json.dumps({"schema_version": 1, "created_at": now_utc(), "status": "planned", "attempts": []}, indent=2) + "\n", encoding="utf-8")
    render_script(run_dir)
    return run_dir


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Unable to read {path}: {exc}") from exc


def render_script(run_dir: Path) -> Path:
    params_path = run_dir.expanduser().resolve() / "params.json"
    if not params_path.is_file():
        raise PipelineError(f"Missing PixInsight params: {params_path}")
    template = PJSR_TEMPLATE.read_text(encoding="utf-8")
    rendered = template.replace("__ASTRO_PARAMS_JSON__", json.dumps(str(params_path)))
    destination = run_dir / "scripts" / "pixinsight-pipeline.js"
    destination.write_text(rendered, encoding="utf-8")
    return destination


def _update_state(run_dir: Path, result: dict[str, Any], command: list[str]) -> None:
    path = run_dir / "state.json"
    state = _load_json(path)
    state["updated_at"] = now_utc()
    state["status"] = "complete" if result.get("ok") else "failed"
    state.setdefault("attempts", []).append({
        "at": state["updated_at"],
        "launch": command,
        "result": str(Path(result.get("result_path", run_dir / "logs" / "result.json"))),
        "ok": bool(result.get("ok")),
    })
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def execute(run_dir: Path, executable_path: str | None, timeout: int, dry_run: bool) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    params_path = run_dir / "params.json"
    params = _load_json(params_path)
    execution_id = uuid.uuid4().hex
    result_path = run_dir / "logs" / f"result-{execution_id}.json"
    params["execution_id"] = execution_id
    params["result"] = str(result_path)
    params_path.write_text(json.dumps(params, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    script = render_script(run_dir)
    path = pixinsight.find_pixinsight(executable_path)
    if not path and not dry_run:
        raise PipelineError("PixInsight executable was not found")
    command = [str(path or "PixInsight"), f"--execute={script}"]
    if dry_run:
        return {"ok": True, "dry_run": True, "script": str(script), "params": str(params_path), "command": command, "result": str(result_path)}
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise PipelineError(f"Unable to launch PixInsight: {exc}") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result_path.is_file():
            result = _load_json(result_path)
            result["result_path"] = str(result_path)
            _update_state(run_dir, result, command)
            if not result.get("ok"):
                raise PipelineError(f"PJSR failed: {result.get('error', 'unknown error')} (see {result_path})")
            return result
        time.sleep(0.5)
    raise PipelineError(f"Timed out waiting for PJSR result JSON: {result_path}. The adapter requires a running PixInsight GUI instance.")


def create_smoke_run(output_dir: Path) -> Path:
    run_dir = output_dir.expanduser().resolve()
    if run_dir.exists() and any(run_dir.iterdir()):
        raise PipelineError(f"Smoke directory must be new or empty: {run_dir}")
    for name in ("checkpoints", "exports", "logs", "scripts"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    params = default_params(run_dir / "synthetic.xisf", run_dir, False, False)
    params["mode"] = "smoke"
    params["result"] = str(run_dir / "logs" / "result.json")
    (run_dir / "params.json").write_text(json.dumps(params, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "state.json").write_text(json.dumps({"schema_version": 1, "created_at": now_utc(), "status": "planned", "attempts": []}, indent=2) + "\n", encoding="utf-8")
    render_script(run_dir)
    return run_dir


def run(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="astro.py")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--run", type=Path, required=True)
    run_p.add_argument("--pixinsight")
    run_p.add_argument("--timeout", type=int, default=1800)
    run_p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(arguments)
    result = execute(args.run, args.pixinsight, args.timeout, args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
