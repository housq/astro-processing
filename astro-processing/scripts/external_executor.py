#!/usr/bin/env python3
"""Checkpointed execution for optional astrophotography processors."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from adapters import graxpert, setiastro, siril_engine, starnet
from config import load_yaml


ROUTED_STAGES = (
    "satellite_removal",
    "background_extraction",
    "detail_restoration",
    "star_separation",
    "denoise",
)
REVIEW_VERDICTS = ("accept", "reject")
AB_ONLY = {("setiastro", "denoise"), ("setiastro", "star_separation")}


class ExternalExecutionError(RuntimeError):
    pass


def _load_run(run: Path) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    run_dir, _, plan, state = siril_engine.require_run(run)
    marker_path = run_dir / ".astro-run.json"
    snapshot_path = run_dir / "run-config.yaml"
    if not marker_path.is_file() or not snapshot_path.is_file():
        raise ExternalExecutionError(
            f"{run_dir} is not a unified astro-processing run; create it with `astro.py plan`"
        )
    marker = siril_engine.load_json(marker_path)
    if Path(marker.get("run_dir", "")).resolve() != run_dir:
        raise ExternalExecutionError(f"Unified run marker does not match directory: {run_dir}")
    snapshot = load_yaml(snapshot_path)
    return run_dir, snapshot, plan, state, marker


def _group_root(run_dir: Path, plan: dict[str, Any], group_id: str) -> Path:
    siril_engine.group_by_id(plan, group_id)
    return run_dir / "groups" / group_id


def _next_attempt_id(external_root: Path, stage: str) -> str:
    prefix = stage.replace("_", "-")
    numbers: list[int] = []
    for path in external_root.glob(f"{prefix}-*"):
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def _load_params(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    value = siril_engine.load_json(path.expanduser().resolve())
    if not isinstance(value, dict):
        raise ExternalExecutionError("Stage params must be a JSON object")
    return value


def _resolve_processor(
    snapshot: dict[str, Any], stage: str, requested: str | None, ab_candidate: bool
) -> tuple[str, dict[str, Any] | None]:
    processors = snapshot.get("resolved_routing", {}).get("processors", {})
    decision = processors.get(stage)
    if ab_candidate:
        processor = requested or "setiastro"
        if (processor, stage) not in AB_ONLY:
            raise ExternalExecutionError(
                "--ab-candidate is limited to SETI Astro denoise or star-separation candidates"
            )
        return processor, decision
    if not decision:
        raise ExternalExecutionError(
            f"Stage {stage} is absent from this frozen route; create a new plan after configuring it"
        )
    selected = str(decision.get("selected", "disabled"))
    processor = requested or selected
    allowed = {selected, *map(str, decision.get("fallbacks", []))}
    if processor not in allowed:
        raise ExternalExecutionError(
            f"Processor {processor} is not the frozen selection or an allowed fallback for {stage}: {sorted(allowed)}"
        )
    if processor == "disabled":
        raise ExternalExecutionError(
            f"Stage {stage} is disabled in the frozen route; configure and confirm it in a new plan before execution"
        )
    if processor == "main":
        processor = str(snapshot.get("resolved_routing", {}).get("main_backend", {}).get("selected", "main"))
    if processor in {"siril", "pixinsight", "bxt", "nxt", "sxt", "rc_astro"}:
        raise ExternalExecutionError(
            f"The routed stage executor currently handles GraXpert, StarNet, and SETI Astro; {processor} stays on its existing backend path"
        )
    return processor, decision


def _current_capability(
    processor: str, stage: str, snapshot: dict[str, Any], ab_candidate: bool
) -> dict[str, Any]:
    project_root = Path(snapshot.get("project_root") or Path.cwd()).expanduser().resolve()
    if processor == "graxpert":
        capability = graxpert.discover(project_root)
    elif processor == "starnet":
        capability = starnet.discover()
    elif processor == "setiastro":
        capability = setiastro.discover()
    else:
        raise ExternalExecutionError(f"Unsupported external processor: {processor}")
    if not capability.get("available"):
        raise ExternalExecutionError(
            f"{processor} is no longer executable with the required local models/runtime; re-run doctor and preflight"
        )
    stage_map = capability.get("ab_stages", {}) if ab_candidate else capability.get("stages", {})
    if stage_map.get(stage) not in {"validated", "experimental"}:
        raise ExternalExecutionError(
            f"{processor} is installed but its {stage} runtime/model capability is unavailable; re-run doctor and preflight"
        )
    frozen = snapshot.get("capabilities", {}).get(processor, {})
    for key in ("version", "interface"):
        before = frozen.get(key)
        now = capability.get(key)
        if key == "version" and before and not re.search(r"\d+\.\d+\.\d+", str(before)):
            continue
        if before not in (None, "unknown") and now not in (None, "unknown") and before != now:
            raise ExternalExecutionError(
                f"{processor} {key} changed from frozen {before} to {now}; re-run preflight and confirm a new route"
            )
    return capability


def _default_input(group_root: Path, group_state: dict[str, Any], stage: str) -> Path:
    if stage == "denoise":
        candidate = group_state.get("starless_checkpoint") or group_state.get("active_linear_checkpoint")
    else:
        candidate = group_state.get("active_linear_checkpoint")
    candidate = candidate or group_state.get("linear_checkpoint") or group_root / "checkpoints" / "stacked-linear.fit"
    return Path(candidate).expanduser().resolve()


def _reference_paths(paths: list[Path]) -> list[str]:
    resolved: list[str] = []
    for path in paths:
        item = path.expanduser().resolve()
        if not item.exists():
            raise ExternalExecutionError(f"Reference does not exist: {item}")
        resolved.append(str(item))
    return resolved


def _siril_executable(snapshot: dict[str, Any], explicit: str | None) -> Path:
    if explicit:
        return siril_engine.find_siril(explicit)
    frozen = snapshot.get("capabilities", {}).get("siril", {}).get("executable")
    return siril_engine.find_siril(str(frozen) if frozen else None)


def _patch_core_ini(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else ["[core]"]
    if not any(line.strip().lower() == "[core]" for line in lines):
        lines = ["[core]", *lines]
    for key, value in values.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=", re.IGNORECASE)
        replaced = False
        for index, line in enumerate(lines):
            if pattern.match(line.strip()):
                lines[index] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            core_index = next(index for index, line in enumerate(lines) if line.strip().lower() == "[core]")
            lines.insert(core_index + 1, f"{key}={value}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _configure_starnet(run_dir: Path, capability: dict[str, Any]) -> Path:
    config_path = run_dir / "runtime" / "config" / "siril" / "config.1.4.ini"
    _patch_core_ini(config_path, starnet.siril_config_values(capability))
    return config_path


def _run_logged(command_line: list[str], cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command_line,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return completed.returncode


def _fits_summary(path: Path) -> dict[str, Any]:
    header = siril_engine.read_fits_header(path)
    keys = (
        "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "BZERO", "BSCALE",
        "CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2",
        "CD1_1", "CD1_2", "CD2_1", "CD2_2", "CDELT1", "CDELT2",
    )
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "header": {key: header[key] for key in keys if key in header},
    }


def _validate_outputs(input_path: Path, outputs: dict[str, Path]) -> dict[str, Any]:
    input_summary = _fits_summary(input_path)
    input_header = input_summary["header"]
    result: dict[str, Any] = {"input": input_summary, "outputs": {}, "dimensions_match": True}
    for role, path in outputs.items():
        if not path.is_file():
            raise ExternalExecutionError(f"Processor did not create required {role} output: {path}")
        summary = _fits_summary(path)
        result["outputs"][role] = summary
        output_header = summary["header"]
        for key in ("NAXIS1", "NAXIS2", "NAXIS3"):
            if input_header.get(key) != output_header.get(key):
                result["dimensions_match"] = False
    if not result["dimensions_match"]:
        raise ExternalExecutionError("Processor output dimensions/channels do not match its input")
    wcs_keys = ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2")
    result["wcs_preserved"] = all(
        key not in input_header or input_header.get(key) == summary["header"].get(key)
        for summary in result["outputs"].values()
        for key in wcs_keys
    )
    return result


def _preview_outputs(
    run_dir: Path,
    executable: Path,
    attempt_dir: Path,
    outputs: dict[str, Path],
    linear: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    lines = ["requires 1.4.0"]
    previews: dict[str, str] = {}
    stats: dict[str, str] = {}
    for role, output in outputs.items():
        preview_base = attempt_dir / f"preview-{role.replace('_', '-')}"
        stats_path = attempt_dir / f"stats-{role.replace('_', '-')}.json"
        lines += [
            f"load {siril_engine.ssf_quote(output)}",
            f"jsonmetadata {siril_engine.ssf_quote(output)} -stats_from_loaded {siril_engine.ssf_option('-out', stats_path)}",
        ]
        if linear:
            lines.append("autostretch -linked -2.8 0.18")
        lines += [
            "resample -maxdim=2048 -interp=area",
            f"savepng {siril_engine.ssf_quote(preview_base)}",
        ]
        previews[role] = str(preview_base.with_suffix(".png"))
        stats[role] = str(stats_path)
    lines.append("close")
    script = attempt_dir / "preview.ssf"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log = attempt_dir / "preview.log"
    returncode = siril_engine.run_siril(executable, script, log)
    missing = [path for path in map(Path, previews.values()) if not path.is_file()]
    if returncode != 0 or missing:
        raise ExternalExecutionError(f"Siril preview generation failed; inspect {log}")
    return previews, stats


def _normalized_params(processor: str, stage: str, supplied: dict[str, Any], linear: bool) -> dict[str, Any]:
    if processor == "graxpert":
        values = graxpert.default_params(stage)
    elif processor == "setiastro":
        values = setiastro.default_params(stage)
        values["linear"] = linear
    elif processor == "starnet":
        values = {"linear": linear}
    else:
        values = {}
    values.update(supplied)
    return values


def run_stage(
    run: Path,
    group_id: str,
    stage: str,
    requested_processor: str | None,
    input_path: Path | None,
    params_path: Path | None,
    references: list[Path],
    reference_notes: str,
    ab_candidate: bool,
    linear: bool,
    siril: str | None,
    dry_run: bool,
) -> Path:
    if stage not in ROUTED_STAGES:
        raise ExternalExecutionError(f"Unsupported routed stage: {stage}")
    run_dir, snapshot, plan, state, marker = _load_run(run)
    group_root = _group_root(run_dir, plan, group_id)
    group_state = state["groups"][group_id]
    processor, route_decision = _resolve_processor(snapshot, stage, requested_processor, ab_candidate)
    capability = _current_capability(processor, stage, snapshot, ab_candidate)
    source = input_path.expanduser().resolve() if input_path else _default_input(group_root, group_state, stage)
    if not source.is_file() and not dry_run:
        raise ExternalExecutionError(f"Stage input checkpoint does not exist: {source}")
    supplied_params = _load_params(params_path)
    if processor == "graxpert" and "ai_version" not in supplied_params:
        frozen_model = snapshot.get("capabilities", {}).get("graxpert", {}).get("models", {}).get(stage, {})
        if frozen_model.get("selected"):
            supplied_params["ai_version"] = str(frozen_model["selected"])
    params = _normalized_params(processor, stage, supplied_params, linear)
    external_root = group_root / "external"
    external_root.mkdir(parents=True, exist_ok=True)
    attempt_id = _next_attempt_id(external_root, stage)
    attempt_dir = external_root / attempt_id
    attempt_dir.mkdir(parents=False, exist_ok=False)
    params_snapshot = attempt_dir / "params.json"
    siril_engine.atomic_json(params_snapshot, params)
    reference_values = _reference_paths(references)
    record: dict[str, Any] = {
        "id": attempt_id,
        "created_at": siril_engine.now_utc(),
        "status": "planned" if dry_run else "running",
        "stage": stage,
        "processor": processor,
        "candidate_only": ab_candidate,
        "linear_input": linear,
        "input_checkpoint": str(source),
        "params": str(params_snapshot),
        "references": reference_values,
        "reference_notes": reference_notes,
        "route_fingerprint": marker.get("route_fingerprint"),
        "frozen_route_decision": route_decision,
        "capability": {
            key: capability.get(key)
            for key in ("executable", "version", "maturity", "platform", "interface", "runtime_backend", "weights", "models", "command_prefix")
            if key in capability
        },
    }
    group_state.setdefault("processor_attempts", []).append(record)
    siril_engine.update_state(run_dir, state)
    try:
        executable = _siril_executable(snapshot, siril)
        outputs: dict[str, Path]
        if processor == "graxpert":
            command_line, outputs = graxpert.build_command(
                capability, stage, source, attempt_dir / "result", params
            )
            record["command"] = command_line
            record["log"] = str(attempt_dir / "processor.log")
            if not dry_run:
                record["returncode"] = _run_logged(command_line, attempt_dir, Path(record["log"]))
        elif processor == "setiastro":
            command_line, outputs = setiastro.build_command(
                capability, stage, source, attempt_dir / "result.fit", params, ab_candidate=ab_candidate
            )
            record["command"] = command_line
            record["log"] = str(attempt_dir / "processor.log")
            if not dry_run:
                record["returncode"] = _run_logged(command_line, attempt_dir, Path(record["log"]))
        elif processor == "starnet":
            if stage != "star_separation":
                raise ExternalExecutionError("StarNet only supports star_separation")
            config_path = _configure_starnet(run_dir, capability)
            script_text, outputs = starnet.build_siril_script(source, attempt_dir, linear)
            script_path = attempt_dir / "process.ssf"
            script_path.write_text(script_text, encoding="utf-8")
            record["siril_config"] = str(config_path)
            record["script"] = str(script_path)
            record["command"] = [str(executable), "-s", str(script_path)]
            record["log"] = str(attempt_dir / "processor.log")
            if not dry_run:
                record["returncode"] = siril_engine.run_siril(executable, script_path, Path(record["log"]))
        else:
            raise ExternalExecutionError(f"Unsupported external processor: {processor}")
        record["outputs"] = {role: str(path) for role, path in outputs.items()}
        if dry_run:
            record["status"] = "planned"
            siril_engine.update_state(run_dir, state)
            siril_engine.write_report(run_dir)
            return attempt_dir
        if record.get("returncode") != 0:
            raise ExternalExecutionError(
                f"{processor} returned {record.get('returncode')}; inspect {record.get('log')}"
            )
        validation = _validate_outputs(source, outputs)
        previews, stats = _preview_outputs(run_dir, executable, attempt_dir, outputs, linear)
        record.update({
            "validation": validation,
            "previews": previews,
            "stats": stats,
            "status": "needs-review",
            "completed_at": siril_engine.now_utc(),
        })
        siril_engine.update_state(run_dir, state)
        siril_engine.write_report(run_dir)
        return attempt_dir
    except (ExternalExecutionError, graxpert.GraXpertError, starnet.StarNetError, setiastro.SetiAstroError, OSError, subprocess.SubprocessError) as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        record["completed_at"] = siril_engine.now_utc()
        siril_engine.update_state(run_dir, state)
        siril_engine.write_report(run_dir)
        if isinstance(exc, ExternalExecutionError):
            raise
        raise ExternalExecutionError(str(exc)) from exc


def _processor_attempt(group_state: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    for attempt in group_state.get("processor_attempts", []):
        if attempt.get("id") == attempt_id:
            return attempt
    raise ExternalExecutionError(f"Unknown processor attempt: {attempt_id}")


def review_stage(
    run: Path,
    group_id: str,
    attempt_id: str,
    verdict: str,
    notes: str,
    issues: list[str],
    references: list[Path] | None = None,
) -> Path:
    if verdict not in REVIEW_VERDICTS:
        raise ExternalExecutionError(f"Unknown review verdict: {verdict}")
    run_dir, _, plan, state, _ = _load_run(run)
    _group_root(run_dir, plan, group_id)
    group_state = state["groups"][group_id]
    attempt = _processor_attempt(group_state, attempt_id)
    if attempt.get("status") != "needs-review":
        raise ExternalExecutionError(
            f"Attempt {attempt_id} is {attempt.get('status')}, not needs-review"
        )
    if not (notes.strip() or issues):
        raise ExternalExecutionError("A visual review must record notes or at least one issue")
    attempt["visual_review"] = {
        "verdict": verdict,
        "notes": notes,
        "issues": issues,
        "additional_references": _reference_paths(references or []),
        "reviewed_at": siril_engine.now_utc(),
    }
    if verdict == "reject":
        attempt["status"] = "rejected"
    else:
        outputs = {role: Path(path) for role, path in attempt.get("outputs", {}).items()}
        if not attempt.get("validation") or not attempt.get("previews"):
            raise ExternalExecutionError("Attempt lacks completed data-integrity or preview evidence")
        missing_outputs = [path for path in outputs.values() if not path.is_file()]
        if missing_outputs:
            raise ExternalExecutionError(
                "Attempt output disappeared before review: " + ", ".join(map(str, missing_outputs))
            )
        if attempt.get("candidate_only"):
            attempt["status"] = "ab-preferred"
            attempt["promotion"] = "not-promoted; confirm and freeze a route change before operational use"
            siril_engine.update_state(run_dir, state)
            report = siril_engine.write_report(run_dir)
            return report
        stage = str(attempt["stage"])
        if stage == "star_separation":
            if not {"starless", "stars"}.issubset(outputs):
                raise ExternalExecutionError("Star separation cannot be accepted without both starless and star-layer checkpoints")
            group_state["starless_checkpoint"] = str(outputs["starless"])
            group_state["star_layer_checkpoint"] = str(outputs["stars"])
        elif stage == "denoise" and group_state.get("starless_checkpoint") == attempt.get("input_checkpoint"):
            group_state["denoised_starless_checkpoint"] = str(outputs["primary"])
            group_state["starless_checkpoint"] = str(outputs["primary"])
        else:
            group_state["active_linear_checkpoint"] = str(outputs["primary"])
        group_state.setdefault("accepted_processor_attempts", {})[stage] = attempt_id
        attempt["status"] = "accepted"
    siril_engine.update_state(run_dir, state)
    report = siril_engine.write_report(run_dir)
    return report
