#!/usr/bin/env python3
"""Frozen-route PixInsight GUI/PJSR execution with isolated visual-review attempts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from config import load_yaml
from state import fingerprint

from . import pixinsight


class PipelineError(RuntimeError):
    pass


PJSR_TEMPLATE = Path(__file__).resolve().parents[2] / "pjsr" / "pixinsight_pipeline.js"
SUCCESS_MARKER = "ASTRO_PROCESSING_PIXINSIGHT_OK"
PROBE_MARKER = "ASTRO_PROCESSING_PIXINSIGHT_PROBE_OK"
REVIEW_VERDICTS = ("accept", "reject")
TUNABLE_PREFIXES = (
    "stages.background.",
    "stages.color.",
    "stages.rc_astro.bxt.",
    "stages.rc_astro.sxt.",
    "stages.rc_astro.nxt.",
    "stages.finish.",
)
LOCKED_TUNABLES = {
    "stages.background.enabled",
    "stages.color.enabled",
    "stages.rc_astro.enabled",
    "stages.rc_astro.license_confirmed",
    "stages.rc_astro.bxt.model",
    "stages.rc_astro.sxt.model",
    "stages.rc_astro.nxt.model",
    "stages.native_denoise.enabled",
}


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Unable to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"Expected a JSON object in {path}")
    return value


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


def create_run(input_path: Path, output_dir: Path, run_id: str | None) -> Path:
    chosen = run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_dir.expanduser().resolve() / chosen
    if run_dir.exists():
        raise PipelineError(f"Run directory already exists: {run_dir}")
    (run_dir / "attempts").mkdir(parents=True)
    _atomic_json(run_dir / "state.json", {
        "schema_version": 2,
        "created_at": now_utc(),
        "status": "planned",
        "attempts": [],
        "selected_attempt": None,
    })
    return run_dir


def _route_rc_enabled(route: dict[str, Any]) -> bool:
    processors = route.get("processors", {})
    selections = (
        processors.get("deconvolution", {}).get("selected"),
        processors.get("star_separation", {}).get("selected"),
        processors.get("denoise", {}).get("selected"),
    )
    if any(item in {"bxt", "sxt", "nxt"} for item in selections) and selections != ("bxt", "sxt", "nxt"):
        raise PipelineError(f"Partial RC-Astro route is unsupported by the consolidated PJSR pipeline: {selections}")
    return selections == ("bxt", "sxt", "nxt")


def freeze_run(run_dir: Path) -> Path:
    """Freeze executable and route from the just-written unified snapshot."""
    run_dir = run_dir.expanduser().resolve()
    snapshot_path = run_dir / "run-config.yaml"
    marker = _load_json(run_dir / ".astro-run.json")
    snapshot = load_yaml(snapshot_path)
    expected_fingerprint = fingerprint({
        "route": snapshot["resolved_routing"],
        "data_profile": snapshot["data_profile"],
        "support_level": snapshot["support_level"],
    })
    if marker.get("route_fingerprint") != expected_fingerprint or snapshot.get("route_fingerprint") != expected_fingerprint:
        raise PipelineError("Cannot freeze a PixInsight run with a mismatched route fingerprint")
    if marker.get("main_backend") != "pixinsight" or Path(marker.get("run_dir", "")).resolve() != run_dir:
        raise PipelineError("Invalid PixInsight unified run marker")
    preflight = snapshot.get("preflight", {})
    pi_capability = preflight.get("capabilities", {}).get("pixinsight", {})
    executable = pi_capability.get("executable")
    if not executable:
        raise PipelineError("Preflight did not freeze a PixInsight executable")
    route = snapshot["resolved_routing"]
    frozen = {
        "schema_version": 1,
        "created_at": now_utc(),
        "input": str(Path(snapshot["input"]).resolve()),
        "executable": str(Path(executable).resolve()),
        "pixinsight": {
            "version": pi_capability.get("version"),
            "platform": pi_capability.get("platform"),
            "probe": preflight.get("pixinsight_plan", {}).get("runtime_probe"),
        },
        "route": route,
        "route_fingerprint": expected_fingerprint,
        "rc_astro": {
            "enabled": _route_rc_enabled(route),
            "license_confirmed": bool(preflight.get("pixinsight_plan", {}).get("license_confirmed")),
            "discovery": preflight.get("capabilities", {}).get("rc_astro", {}),
        },
    }
    if frozen["rc_astro"]["enabled"] and not frozen["rc_astro"]["license_confirmed"]:
        raise PipelineError("Cannot freeze RC-Astro route without separate license confirmation")
    path = run_dir / "frozen-pixinsight.json"
    _atomic_json(path, frozen)
    return path


def _validate_frozen_run(run_dir: Path) -> dict[str, Any]:
    marker = _load_json(run_dir / ".astro-run.json")
    frozen = _load_json(run_dir / "frozen-pixinsight.json")
    snapshot = load_yaml(run_dir / "run-config.yaml")
    expected = fingerprint({
        "route": snapshot["resolved_routing"],
        "data_profile": snapshot["data_profile"],
        "support_level": snapshot["support_level"],
    })
    values = {expected, marker.get("route_fingerprint"), snapshot.get("route_fingerprint"), frozen.get("route_fingerprint")}
    if len(values) != 1:
        raise PipelineError("Frozen PixInsight route fingerprint does not match the run snapshot")
    if marker.get("main_backend") != "pixinsight" or Path(marker.get("run_dir", "")).resolve() != run_dir:
        raise PipelineError("PixInsight run marker path/backend mismatch")
    if frozen.get("route") != snapshot.get("resolved_routing"):
        raise PipelineError("Frozen PixInsight route has been modified")
    frozen_executable = Path(frozen["executable"])
    if frozen_executable != frozen_executable.resolve():
        raise PipelineError("Frozen PixInsight executable is not an absolute resolved path")
    return frozen


def default_params(frozen: dict[str, Any], attempt_dir: Path, execution_id: str, mode: str = "pipeline") -> dict[str, Any]:
    checkpoints, exports, previews, logs = (
        attempt_dir / "checkpoints",
        attempt_dir / "exports",
        attempt_dir / "previews",
        attempt_dir / "logs",
    )
    rc_enabled = bool(frozen.get("rc_astro", {}).get("enabled"))
    return {
        "schema_version": 2,
        "mode": mode,
        "execution_id": execution_id,
        "route_fingerprint": frozen.get("route_fingerprint"),
        "frozen_executable": frozen.get("executable"),
        "input": frozen["input"],
        "input_state": "integrated-linear",
        "data_type": "osc-color",
        "result": str(logs / "result.json"),
        "console_log": str(logs / "pixinsight-console.log"),
        "output": {"checkpoints": str(checkpoints), "exports": str(exports), "previews": str(previews)},
        "stages": {
            "background": {"enabled": True, "method": "ABE", "degree": 1, "tolerance": 1.0, "deviation": 0.8, "box_size": 10, "box_separation": 10, "downsample": 2.0},
            "color": {"enabled": True, "method": "classic", "background_low": 0.0, "background_high": 0.10, "target_background": 0.001, "structure_layers": 5, "noise_layers": 1, "white_low": 0.0, "white_high": 0.90},
            "rc_astro": {
                "enabled": rc_enabled,
                "license_confirmed": bool(frozen.get("rc_astro", {}).get("license_confirmed")),
                "bxt": {"model": "BlurXTerminator.4.mlpackage", "correct_first": False, "sharpen_stars": 0.30, "sharpen_nonstellar": 0.35, "adjust_halos": 0.0},
                "sxt": {"model": "StarXTerminator.lite.nonoise.11.mlpackage", "overlap": 0.20, "unscreen": False},
                "nxt": {"model": "NoiseXTerminator.3.mlpackage", "denoise": 0.55, "denoise_color": 0.65, "iterations": 2, "detail": 0.20},
            },
            "native_denoise": {"enabled": not rc_enabled, "method": "MLT"},
            "finish": {"enabled": True, "starless_midtones": 0.0045, "stars_midtones": 0.012, "single_midtones": 0.005254792191279071, "starless_saturation": 0.12, "stars_saturation": 0.04, "single_saturation": 0.08, "star_strength": 0.70},
            "exports": {"xisf": True, "fits32": True, "tiff": True, "png": True, "jpeg": True, "verify_reopen": True},
        },
        "limitations": {"spcc": "skipped", "image_solver": "not automated", "dbe": "not implemented", "wbpp": "not implemented"},
    }


def _flatten(mapping: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in mapping.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, dotted))
        else:
            result[dotted] = value
    return result


def _set_dotted(mapping: dict[str, Any], dotted: str, value: Any) -> None:
    current = mapping
    parts = dotted.split(".")
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def apply_tuning(params: dict[str, Any], override_path: Path | None) -> dict[str, Any]:
    if not override_path:
        return params
    override = _load_json(override_path.expanduser().resolve())
    for dotted, value in _flatten(override).items():
        if dotted in LOCKED_TUNABLES or not any(dotted.startswith(prefix) for prefix in TUNABLE_PREFIXES):
            raise PipelineError(f"Tuning cannot modify frozen routing/execution field: {dotted}")
        if dotted not in _flatten(params):
            raise PipelineError(f"Unknown PixInsight tuning field: {dotted}")
        _set_dotted(params, dotted, value)
    return params


def render_script(params_path: Path, destination: Path) -> Path:
    template = PJSR_TEMPLATE.read_text(encoding="utf-8")
    rendered = template.replace("__ASTRO_PARAMS_JSON__", json.dumps(str(params_path.resolve())))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return destination


def _new_attempt(run_dir: Path, kind: str = "pipeline") -> tuple[str, Path]:
    attempt_id = f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    attempt_dir = run_dir / "attempts" / attempt_id
    for name in ("checkpoints", "exports", "previews", "logs", "scripts"):
        (attempt_dir / name).mkdir(parents=True, exist_ok=False)
    _atomic_json(attempt_dir / "attempt.json", {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "kind": kind,
        "created_at": now_utc(),
        "status": "prepared",
    })
    return attempt_id, attempt_dir


def _update_attempt(attempt_dir: Path, **changes: Any) -> dict[str, Any]:
    path = attempt_dir / "attempt.json"
    attempt = _load_json(path)
    attempt.update(changes)
    attempt["updated_at"] = now_utc()
    _atomic_json(path, attempt)
    return attempt


def _update_run(run_dir: Path, attempt_id: str, status: str) -> None:
    path = run_dir / "state.json"
    state = _load_json(path)
    if attempt_id not in state.setdefault("attempts", []):
        state["attempts"].append(attempt_id)
    state["status"] = status
    state["updated_at"] = now_utc()
    _atomic_json(path, state)


def _record_late_results(run_dir: Path) -> None:
    for attempt_path in sorted((run_dir / "attempts").glob("*/attempt.json")):
        attempt = _load_json(attempt_path)
        if attempt.get("status") not in {"timed_out", "failed"} or attempt.get("late_result"):
            continue
        result_path = attempt_path.parent / "logs" / "result.json"
        if not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            attempt["late_result_observation"] = {
                "recorded_at": now_utc(),
                "complete_json": False,
                "quarantined": True,
                "error": type(exc).__name__,
            }
            _atomic_json(attempt_path, attempt)
            continue
        attempt["late_result"] = {
            "recorded_at": now_utc(),
            "ok": result.get("ok") is True,
            "execution_id_matches": result.get("execution_id") == attempt.get("execution_id"),
            "success_marker_matches": result.get("successMarker") == SUCCESS_MARKER,
            "quarantined": True,
        }
        _atomic_json(attempt_path, attempt)


def _read_result_if_complete(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _paths_named(value: Any, name: str) -> list[Path]:
    result: list[Path] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == name and isinstance(child, str):
                result.append(Path(child))
            else:
                result.extend(_paths_named(child, name))
    elif isinstance(value, list):
        for child in value:
            result.extend(_paths_named(child, name))
    return result


def validate_result(
    result: dict[str, Any],
    execution_id: str,
    attempt_dir: Path,
    marker: str = SUCCESS_MARKER,
    route_fingerprint: str | None = None,
    executable: Path | None = None,
    pixinsight_tuple: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if result.get("execution_id") != execution_id:
        raise PipelineError("PJSR result execution_id does not match this attempt")
    if result.get("ok") is not True:
        raise PipelineError(f"PJSR failed: {result.get('error', 'unknown error')}")
    if result.get("successMarker") != marker:
        raise PipelineError("PJSR result successMarker is absent or invalid")
    execution = result.get("execution", {})
    if route_fingerprint is not None and execution.get("routeFingerprint") != route_fingerprint:
        raise PipelineError("PJSR result route fingerprint does not match the frozen attempt")
    if executable is not None:
        recorded = execution.get("frozenExecutable")
        if not recorded or Path(recorded).resolve() != executable.resolve():
            raise PipelineError("PJSR result executable does not match the frozen attempt")
    if pixinsight_tuple:
        actual_pi = result.get("pixinsight", {})
        for field in ("versionMajor", "versionMinor", "versionRelease", "versionRevision", "versionBuild"):
            if actual_pi.get(field) != pixinsight_tuple.get(field):
                raise PipelineError(f"PJSR result PixInsight {field} does not match the frozen runtime probe")
    root = attempt_dir.resolve()
    paths = _paths_named(result.get("exports", {}), "path") + [Path(item) for item in result.get("previews", [])]
    if marker == SUCCESS_MARKER:
        if not paths or not result.get("previews"):
            raise PipelineError("PJSR success omitted export or preview evidence")
        for path in paths:
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise PipelineError(f"PJSR reported missing or out-of-attempt artifact: {path}")
    return result


def validate_runtime_probe(path: Path, expected_executable: Path) -> dict[str, Any]:
    """Validate a prior authenticated probe without treating it as a license check."""
    result = _load_json(path.expanduser().resolve())
    if result.get("ok") is not True or result.get("successMarker") != PROBE_MARKER:
        raise PipelineError("PixInsight runtime probe is not an authenticated successful probe")
    execution_id = result.get("execution_id")
    if not isinstance(execution_id, str) or len(execution_id) < 16:
        raise PipelineError("PixInsight runtime probe has an invalid execution_id")
    recorded = result.get("execution", {}).get("frozenExecutable")
    if not recorded or Path(recorded).resolve() != expected_executable.resolve():
        raise PipelineError("PixInsight runtime probe executable does not match the requested executable")
    for key in ("bxt", "sxt", "nxt"):
        module = result.get("rc_astro", {}).get(key, {})
        expected_model = pixinsight.RC_ASTRO_MODULES[key]["expected_model"]
        if module.get("available") is not True or module.get("ai_file") != expected_model:
            raise PipelineError(f"PixInsight runtime probe did not construct {key.upper()} with a model")
    return result


def _standalone_pixinsight(
    mode: str,
    executable_path: str | None,
    output_dir: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    """Run a probe or synthetic smoke test in its own immutable attempt."""
    executable = pixinsight.find_pixinsight(executable_path)
    if not executable and not dry_run:
        raise PipelineError("PixInsight executable was not found for capability probe")
    executable = Path(executable or executable_path or "PixInsight").expanduser().resolve()
    probe_root = output_dir.expanduser().resolve() / f"{mode}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    attempt_id, attempt_dir = _new_attempt(probe_root, mode)
    execution_id = uuid.uuid4().hex
    frozen = {
        "input": str(attempt_dir / "synthetic.xisf"),
        "executable": str(executable),
        "route_fingerprint": "capability-probe",
        "rc_astro": {"enabled": False, "license_confirmed": False},
    }
    params = default_params(frozen, attempt_dir, execution_id, mode)
    params_path = attempt_dir / "params.json"
    _atomic_json(params_path, params)
    script = render_script(params_path, attempt_dir / "scripts" / "pixinsight-pipeline.js")
    result_path = attempt_dir / "logs" / "result.json"
    command = [str(executable), f"--execute={script}"]
    _update_attempt(
        attempt_dir,
        status="dry_run" if dry_run else "running",
        execution_id=execution_id,
        executable=str(executable),
        command=command,
        result=str(result_path),
    )
    if dry_run:
        return {"ok": True, "dry_run": True, "attempt_id": attempt_id, "script": str(script), "params": str(params_path), "command": command}
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        _update_attempt(attempt_dir, status="failed", failure_kind="launch", error=str(exc))
        raise PipelineError(f"Unable to launch PixInsight probe: {exc}") from exc
    deadline = time.monotonic() + timeout
    malformed_seen = False
    while time.monotonic() < deadline:
        if result_path.is_file():
            result = _read_result_if_complete(result_path)
            if result is None:
                malformed_seen = True
                time.sleep(0.25)
                continue
            try:
                expected_marker = PROBE_MARKER if mode == "probe" else SUCCESS_MARKER
                validate_result(result, execution_id, attempt_dir, expected_marker)
                if mode == "probe":
                    validate_runtime_probe(result_path, executable)
            except PipelineError as exc:
                _update_attempt(attempt_dir, status="failed", failure_kind="probe_validation", error=str(exc))
                raise
            _update_attempt(attempt_dir, status="complete", incomplete_json_observed=malformed_seen)
            result.update({"attempt_id": attempt_id, "result_path": str(result_path)})
            return result
        time.sleep(0.5)
    _update_attempt(attempt_dir, status="timed_out", failure_kind="timeout", incomplete_json_observed=malformed_seen)
    raise PipelineError(f"Timed out waiting for PixInsight {mode}: {result_path}")


def probe_pixinsight(executable_path: str | None, output_dir: Path, timeout: int, dry_run: bool) -> dict[str, Any]:
    return _standalone_pixinsight("probe", executable_path, output_dir, timeout, dry_run)


def smoke_pixinsight(executable_path: str | None, output_dir: Path, timeout: int, dry_run: bool) -> dict[str, Any]:
    return _standalone_pixinsight("smoke", executable_path, output_dir, timeout, dry_run)


def _validate_current_executable(frozen: dict[str, Any], override: str | None, dry_run: bool) -> Path:
    frozen_path = Path(frozen["executable"])
    if override and Path(override).expanduser().resolve() != frozen_path:
        raise PipelineError("--pixinsight does not match the executable frozen at plan time")
    if dry_run:
        return frozen_path
    if not frozen_path.is_file():
        raise PipelineError(f"Frozen PixInsight executable is unavailable: {frozen_path}")
    current = pixinsight.discover(str(frozen_path))
    if Path(current.get("executable", "")).resolve() != frozen_path:
        raise PipelineError("PixInsight discovery no longer resolves to the frozen executable")
    for field in ("version", "platform"):
        expected = frozen.get("pixinsight", {}).get(field)
        if expected and current.get(field) != expected:
            raise PipelineError(f"Frozen PixInsight {field} changed: {expected!r} -> {current.get(field)!r}")
    return frozen_path


def execute(
    run_dir: Path,
    executable_path: str | None,
    timeout: int,
    dry_run: bool,
    tuning_path: Path | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    frozen = _validate_frozen_run(run_dir)
    _record_late_results(run_dir)
    state = _load_json(run_dir / "state.json")
    if state.get("status") == "running":
        raise PipelineError("A PixInsight attempt is already running; reconcile or wait for it before retrying")
    if state.get("status") == "needs_review":
        raise PipelineError("The latest PixInsight attempt needs visual accept/reject review before retrying")
    attempt_id, attempt_dir = _new_attempt(run_dir)
    execution_id = uuid.uuid4().hex
    try:
        executable = _validate_current_executable(frozen, executable_path, dry_run)
        params = apply_tuning(default_params(frozen, attempt_dir, execution_id), tuning_path)
        params_path = attempt_dir / "params.json"
        _atomic_json(params_path, params)
        script = render_script(params_path, attempt_dir / "scripts" / "pixinsight-pipeline.js")
        result_path = attempt_dir / "logs" / "result.json"
        command = [str(executable), f"--execute={script}"]
    except (OSError, PipelineError) as exc:
        _update_attempt(attempt_dir, status="failed", execution_id=execution_id, failure_kind="preparation", error=str(exc))
        _update_run(run_dir, attempt_id, "failed")
        raise
    _update_attempt(
        attempt_dir,
        status="dry_run" if dry_run else "launching",
        execution_id=execution_id,
        route_fingerprint=frozen["route_fingerprint"],
        executable=str(executable),
        command=command,
        params=str(params_path),
        result=str(result_path),
    )
    _update_run(run_dir, attempt_id, "planned" if dry_run else "running")
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "attempt_id": attempt_id,
            "attempt_dir": str(attempt_dir),
            "script": str(script),
            "params": str(params_path),
            "command": command,
            "result": str(result_path),
        }
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        _update_attempt(attempt_dir, status="failed", failure_kind="launch", error=str(exc))
        _update_run(run_dir, attempt_id, "failed")
        raise PipelineError(f"Unable to launch PixInsight: {exc}") from exc
    _update_attempt(attempt_dir, status="running", launched_at=now_utc())
    deadline = time.monotonic() + timeout
    malformed_seen = False
    while time.monotonic() < deadline:
        if result_path.is_file():
            result = _read_result_if_complete(result_path)
            if result is None:
                malformed_seen = True
                time.sleep(0.25)
                continue
            try:
                validate_result(
                    result,
                    execution_id,
                    attempt_dir,
                    SUCCESS_MARKER,
                    frozen["route_fingerprint"],
                    executable,
                    (frozen.get("pixinsight", {}).get("probe") or {}).get("pixinsight"),
                )
            except PipelineError as exc:
                _update_attempt(attempt_dir, status="failed", failure_kind="result_validation", error=str(exc))
                _update_run(run_dir, attempt_id, "failed")
                raise
            result["result_path"] = str(result_path)
            result["attempt_id"] = attempt_id
            _update_attempt(attempt_dir, status="needs_review", technical_success_at=now_utc(), incomplete_json_observed=malformed_seen)
            _update_run(run_dir, attempt_id, "needs_review")
            return result
        time.sleep(0.5)
    _update_attempt(attempt_dir, status="timed_out", failure_kind="timeout", incomplete_json_observed=malformed_seen)
    _update_run(run_dir, attempt_id, "failed")
    raise PipelineError(
        f"Timed out waiting for PJSR result JSON: {result_path}. Any late result remains quarantined in this attempt."
    )


def review_attempt(
    run_dir: Path,
    attempt_id: str,
    verdict: str,
    notes: str,
    issues: list[str],
) -> str:
    if verdict not in REVIEW_VERDICTS:
        raise PipelineError(f"Unsupported review verdict: {verdict}")
    run_dir = run_dir.expanduser().resolve()
    _validate_frozen_run(run_dir)
    attempt_dir = (run_dir / "attempts" / attempt_id).resolve()
    if not attempt_dir.is_relative_to((run_dir / "attempts").resolve()) or not attempt_dir.is_dir():
        raise PipelineError(f"Unknown PixInsight attempt: {attempt_id}")
    attempt = _load_json(attempt_dir / "attempt.json")
    if attempt.get("status") != "needs_review":
        raise PipelineError(f"Attempt {attempt_id} is {attempt.get('status')}, not needs_review")
    result = _load_json(attempt_dir / "logs" / "result.json")
    frozen = _load_json(run_dir / "frozen-pixinsight.json")
    validate_result(
        result,
        attempt["execution_id"],
        attempt_dir,
        SUCCESS_MARKER,
        attempt.get("route_fingerprint"),
        Path(frozen["executable"]),
        (frozen.get("pixinsight", {}).get("probe") or {}).get("pixinsight"),
    )
    review = {"verdict": verdict, "reviewed_at": now_utc(), "notes": notes, "issues": list(issues)}
    _atomic_json(attempt_dir / "review.json", review)
    status = "accepted" if verdict == "accept" else "rejected"
    _update_attempt(attempt_dir, status=status, review=review)
    state_path = run_dir / "state.json"
    state = _load_json(state_path)
    state["status"] = "complete" if verdict == "accept" else "rejected"
    state["selected_attempt"] = attempt_id if verdict == "accept" else None
    state["updated_at"] = now_utc()
    _atomic_json(state_path, state)
    return f"PixInsight attempt {attempt_id} {status}; run status={state['status']}"


def reconcile_attempt(run_dir: Path, attempt_id: str) -> str:
    """Authenticate a result produced after the launcher stopped polling."""
    run_dir = run_dir.expanduser().resolve()
    _validate_frozen_run(run_dir)
    attempt_dir = (run_dir / "attempts" / attempt_id).resolve()
    if not attempt_dir.is_relative_to((run_dir / "attempts").resolve()) or not attempt_dir.is_dir():
        raise PipelineError(f"Unknown PixInsight attempt: {attempt_id}")
    attempt = _load_json(attempt_dir / "attempt.json")
    if attempt.get("status") in {"timed_out", "failed"}:
        _record_late_results(run_dir)
        refreshed = _load_json(attempt_dir / "attempt.json")
        if refreshed.get("late_result"):
            return f"Late result for {attempt_id} recorded as quarantined; attempt remains {refreshed['status']}"
        raise PipelineError(f"Attempt {attempt_id} has no complete late result to record")
    if attempt.get("status") != "running":
        return f"Attempt {attempt_id} is already {attempt.get('status')}"
    result_path = attempt_dir / "logs" / "result.json"
    result = _read_result_if_complete(result_path)
    if result is None:
        raise PipelineError(f"Attempt {attempt_id} has no complete result JSON")
    try:
        frozen = _load_json(run_dir / "frozen-pixinsight.json")
        validate_result(
            result,
            attempt["execution_id"],
            attempt_dir,
            SUCCESS_MARKER,
            attempt.get("route_fingerprint"),
            Path(frozen["executable"]),
            (frozen.get("pixinsight", {}).get("probe") or {}).get("pixinsight"),
        )
    except PipelineError as exc:
        _update_attempt(attempt_dir, status="failed", failure_kind="result_validation", error=str(exc))
        _update_run(run_dir, attempt_id, "failed")
        raise
    _update_attempt(attempt_dir, status="needs_review", technical_success_at=now_utc(), reconciled=True)
    _update_run(run_dir, attempt_id, "needs_review")
    return f"Authenticated result for {attempt_id}; run status=needs_review"


def run(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="astro.py")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--run", type=Path, required=True)
    run_p.add_argument("--pixinsight")
    run_p.add_argument("--params", type=Path, help="JSON containing only allowlisted visual tuning fields")
    run_p.add_argument("--timeout", type=int, default=1800)
    run_p.add_argument("--dry-run", action="store_true")
    review_p = sub.add_parser("review-pixinsight")
    review_p.add_argument("--run", type=Path, required=True)
    review_p.add_argument("--attempt", required=True)
    review_p.add_argument("--verdict", choices=REVIEW_VERDICTS, required=True)
    review_p.add_argument("--notes", default="")
    review_p.add_argument("--issue", action="append", default=[])
    reconcile_p = sub.add_parser("reconcile-pixinsight")
    reconcile_p.add_argument("--run", type=Path, required=True)
    reconcile_p.add_argument("--attempt", required=True)
    probe_p = sub.add_parser("probe-pixinsight")
    probe_p.add_argument("--output", type=Path, required=True)
    probe_p.add_argument("--pixinsight")
    probe_p.add_argument("--timeout", type=int, default=300)
    probe_p.add_argument("--dry-run", action="store_true")
    smoke_p = sub.add_parser("smoke-pixinsight")
    smoke_p.add_argument("--output", type=Path, required=True)
    smoke_p.add_argument("--pixinsight")
    smoke_p.add_argument("--timeout", type=int, default=300)
    smoke_p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(arguments)
    if args.command == "review-pixinsight":
        print(review_attempt(args.run, args.attempt, args.verdict, args.notes, args.issue))
        return 0
    if args.command == "reconcile-pixinsight":
        print(reconcile_attempt(args.run, args.attempt))
        return 0
    if args.command == "probe-pixinsight":
        print(json.dumps(probe_pixinsight(args.pixinsight, args.output, args.timeout, args.dry_run), indent=2, sort_keys=True))
        return 0
    if args.command == "smoke-pixinsight":
        print(json.dumps(smoke_pixinsight(args.pixinsight, args.output, args.timeout, args.dry_run), indent=2, sort_keys=True))
        return 0
    result = execute(args.run, args.pixinsight, args.timeout, args.dry_run, args.params)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
