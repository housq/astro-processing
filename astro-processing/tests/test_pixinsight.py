#!/usr/bin/env python3
"""Dependency-free PixInsight frozen-route, attempt, and result-authenticity tests."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import astro as astro_module  # noqa: E402
from adapters import pixinsight, pixinsight_engine  # noqa: E402
from astro import main as astro_main  # noqa: E402
from state import write_run_snapshot  # noqa: E402


def fake_xisf(root: Path) -> Path:
    path = root / "integrated-linear.xisf"
    path.write_bytes(b"test-only-placeholder")
    return path


def route(rc_enabled: bool = False) -> dict:
    selected = {
        "satellite_removal": "disabled",
        "background_extraction": "pixinsight",
        "deconvolution": "bxt" if rc_enabled else "disabled",
        "detail_restoration": "disabled",
        "star_separation": "sxt" if rc_enabled else "disabled",
        "denoise": "nxt" if rc_enabled else "pixinsight",
    }
    return {
        "execution_profile": "balanced",
        "main_backend": {"selected": "pixinsight", "maturity": "experimental"},
        "processors": {
            stage: {"selected": processor, "mode": "require" if processor != "disabled" else "disabled"}
            for stage, processor in selected.items()
        },
        "confirmation_reasons": ["test route"],
    }


def frozen_run(root: Path, rc_enabled: bool = False) -> Path:
    source = fake_xisf(root)
    executable = root / "PixInsight"
    executable.write_text("test executable", encoding="utf-8")
    run_dir = pixinsight_engine.create_run(source, root / "output", "run")
    resolved_route = route(rc_enabled)
    capabilities = {
        "pixinsight": {
            "available": True,
            "installed": True,
            "executable": str(executable.resolve()),
            "version": "1.8.9-3",
            "platform": "test",
            "maturity": "experimental",
            "stages": {},
        },
        "rc_astro": {"installed": rc_enabled, "available": rc_enabled, "license": "active" if rc_enabled else "unconfirmed"},
    }
    preflight = {
        "capabilities": capabilities,
        "pixinsight_plan": {"license_confirmed": rc_enabled, "runtime_probe": None},
    }
    write_run_snapshot(
        run_dir,
        source,
        root,
        {"schema_version": 1},
        {},
        [],
        capabilities,
        resolved_route,
        preflight,
        "post-integration-osc-color",
        "experimental",
    )
    pixinsight_engine.freeze_run(run_dir)
    return run_dir


def successful_result(
    params: dict,
    attempt_dir: Path,
    marker: str = pixinsight_engine.SUCCESS_MARKER,
    rc_enabled: bool | None = None,
) -> dict:
    rc_enabled = bool(params.get("stages", {}).get("rc_astro", {}).get("enabled")) if rc_enabled is None else rc_enabled
    attempt_dir.joinpath("exports").mkdir(parents=True, exist_ok=True)
    attempt_dir.joinpath("previews").mkdir(parents=True, exist_ok=True)
    files = []
    for suffix, (bits, is_real) in pixinsight_engine.EXPORT_CONTRACT.items():
        path = attempt_dir / "exports" / f"final{suffix}"
        path.write_bytes(b"artifact")
        files.append({
            "path": str(path),
            "sameDimensions": True,
            "sameChannels": True,
            "bitDepthOK": True,
            "orientationOK": True,
            "wcsPreserved": True,
            "width": 96,
            "height": 64,
            "channels": 3,
            "bitsPerSample": bits,
            "isReal": is_real,
            "linear": False,
        })
    preview_names = pixinsight_engine.PIPELINE_PREVIEWS[rc_enabled]
    previews = []
    for name in sorted(preview_names):
        path = attempt_dir / "previews" / name
        path.write_bytes(b"preview")
        previews.append(str(path))
    stage_evidence: dict[str, list[str]] = {
        "background-extraction": ["01-abe-linear.xisf", "01-abe-model.xisf"],
        "color-calibration": ["02-bn-linear.xisf", "02-color-linear.xisf"],
        "denoise": ["05-nxt-starless-linear.xisf" if rc_enabled else "03-mlt-linear.xisf"],
    }
    if rc_enabled:
        stage_evidence.update({
            "deconvolution": ["03-bxt-linear.xisf"],
            "star-separation": ["04-starless-linear.xisf", "04-stars-linear.xisf"],
        })
    checkpoints = attempt_dir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    stages = []
    for name in pixinsight_engine.PIPELINE_STAGES[rc_enabled]:
        paths = []
        for filename in stage_evidence.get(name, []):
            path = checkpoints / filename
            path.write_bytes(b"checkpoint")
            paths.append({"path": str(path)})
        stage = {"stage": name, "ok": True}
        if paths:
            stage["evidence"] = {"artifacts": paths}
        stages.append(stage)
    return {
        "schema_version": 1,
        "mode": "pipeline",
        "ok": True,
        "execution_id": params["execution_id"],
        "successMarker": marker,
        "pixinsight": {
            "versionMajor": 1,
            "versionMinor": 8,
            "versionRelease": 9,
            "versionRevision": 3,
            "versionBuild": 0,
        },
        "execution": {
            "frozenExecutable": params.get("frozen_executable"),
            "routeFingerprint": params.get("route_fingerprint"),
            "params": str(attempt_dir / "params.json"),
        },
        "input": {
            "width": 96,
            "height": 64,
            "channels": 3,
            "bitsPerSample": 32,
            "isColor": True,
            "isReal": True,
        },
        "stages": stages,
        "exports": {
            "files": files,
            "fitsBoundary": {"format": "FITS", "bitsPerSample": 32, "isReal": True},
        },
        "previews": previews,
    }


class FakePopen:
    def __init__(self, command: list[str], **_: object) -> None:
        script = Path(command[1].split("=", 1)[1])
        attempt_dir = script.parents[1]
        params = json.loads((attempt_dir / "params.json").read_text(encoding="utf-8"))
        (attempt_dir / "logs" / "result.json").write_text(
            json.dumps(successful_result(params, attempt_dir)), encoding="utf-8"
        )


def run_with_fake_pjsr(run_dir: Path, tuning: Path | None = None) -> dict:
    original_popen = pixinsight_engine.subprocess.Popen
    original_validate = pixinsight_engine._validate_current_executable
    try:
        pixinsight_engine.subprocess.Popen = FakePopen
        pixinsight_engine._validate_current_executable = lambda frozen, override, dry: Path(frozen["executable"])
        return pixinsight_engine.execute(run_dir, None, 2, False, tuning)
    finally:
        pixinsight_engine.subprocess.Popen = original_popen
        pixinsight_engine._validate_current_executable = original_validate


def expect_pipeline_error(callable_: object, contains: str) -> None:
    try:
        callable_()
    except pixinsight_engine.PipelineError as exc:
        assert contains in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected PipelineError containing {contains!r}")


def test_declared_input_contract() -> None:
    with tempfile.TemporaryDirectory() as temp:
        profile, blockers = pixinsight_engine.inspect_declared_input(fake_xisf(Path(temp)), "integrated-linear", "osc-color")
        assert not blockers
        assert profile["declared_linear"] is True


def test_unknown_linearity_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as temp:
        _, blockers = pixinsight_engine.inspect_declared_input(fake_xisf(Path(temp)), "unknown", "osc-color")
        assert any(item["code"] == "PIXINSIGHT_INPUT_STATE_REQUIRED" for item in blockers)


def test_dry_run_generates_an_isolated_relocatable_attempt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        first = pixinsight_engine.execute(run_dir, None, 1, True)
        second = pixinsight_engine.execute(run_dir, None, 1, True)
        assert first["attempt_id"] != second["attempt_id"]
        assert Path(first["attempt_dir"]).parent == run_dir / "attempts"
        assert Path(first["params"]).parent == Path(first["attempt_dir"])
        script = Path(first["script"]).read_text(encoding="utf-8")
        assert "__ASTRO_PARAMS_JSON__" not in script
        assert "new BlurXTerminator" in script


def test_technical_success_needs_review_and_accept_completes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        result = run_with_fake_pjsr(run_dir)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state["status"] == "needs_review"
        attempt = json.loads((run_dir / "attempts" / result["attempt_id"] / "attempt.json").read_text(encoding="utf-8"))
        assert attempt["status"] == "needs_review"
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "needs_review")
        expect_pipeline_error(
            lambda: pixinsight_engine.review_attempt(run_dir, result["attempt_id"], "accept", "", []),
            "Visual review",
        )
        pixinsight_engine.review_attempt(run_dir, result["attempt_id"], "accept", "visual checks passed", [])
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state["status"] == "complete"
        assert state["selected_attempt"] == result["attempt_id"]
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "run is complete")


def test_visual_reject_tune_new_attempt_then_accept() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        run_dir = frozen_run(root)
        first = run_with_fake_pjsr(run_dir)
        pixinsight_engine.review_attempt(run_dir, first["attempt_id"], "reject", "too bright", ["highlight clipping"])
        tuning = root / "tuning.json"
        tuning.write_text(json.dumps({"stages": {"finish": {"single_midtones": 0.006}}}), encoding="utf-8")
        second = run_with_fake_pjsr(run_dir, tuning)
        assert second["attempt_id"] != first["attempt_id"]
        first_params = json.loads((run_dir / "attempts" / first["attempt_id"] / "params.json").read_text(encoding="utf-8"))
        second_params = json.loads((run_dir / "attempts" / second["attempt_id"] / "params.json").read_text(encoding="utf-8"))
        assert first_params["stages"]["finish"]["single_midtones"] != second_params["stages"]["finish"]["single_midtones"]
        pixinsight_engine.review_attempt(run_dir, second["attempt_id"], "accept", "balanced", [])
        assert json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["selected_attempt"] == second["attempt_id"]


def test_modified_attempt_parameters_cannot_be_reviewed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        result = run_with_fake_pjsr(run_dir)
        params_path = run_dir / "attempts" / result["attempt_id"] / "params.json"
        params_path.chmod(0o644)
        params = json.loads(params_path.read_text(encoding="utf-8"))
        params["input"] = str(Path(temp) / "different.xisf")
        params_path.write_text(json.dumps(params), encoding="utf-8")
        expect_pipeline_error(
            lambda: pixinsight_engine.review_attempt(run_dir, result["attempt_id"], "accept", "looks good", []),
            "parameters have been modified",
        )


def test_locked_route_fields_cannot_be_tuned() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        run_dir = frozen_run(root)
        tuning = root / "bad.json"
        tuning.write_text(json.dumps({"stages": {"rc_astro": {"enabled": True}}}), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True, tuning), "frozen routing")


def test_frozen_executable_override_is_rejected_and_recorded() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        expect_pipeline_error(
            lambda: pixinsight_engine.execute(run_dir, "/different/PixInsight", 1, True),
            "does not match",
        )
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state["status"] == "failed"
        attempt = json.loads((run_dir / "attempts" / state["attempts"][-1] / "attempt.json").read_text(encoding="utf-8"))
        assert attempt["failure_kind"] == "preparation"


def test_route_fingerprint_tampering_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        frozen = json.loads((run_dir / "frozen-pixinsight.json").read_text(encoding="utf-8"))
        frozen["route"]["processors"]["denoise"]["selected"] = "nxt"
        (run_dir / "frozen-pixinsight.json").write_text(json.dumps(frozen), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "execution contract")


def test_frozen_input_and_rc_contract_tampering_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        run_dir = frozen_run(root)
        frozen_path = run_dir / "frozen-pixinsight.json"
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        replacement = root / "replacement.xisf"
        replacement.write_bytes(b"different input")
        frozen["input"] = str(replacement)
        frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "execution contract")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        run_dir = frozen_run(root)
        source = root / "integrated-linear.xisf"
        source.write_bytes(b"same path, changed pixels")
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "execution contract")

    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        frozen_path = run_dir / "frozen-pixinsight.json"
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        frozen["rc_astro"]["enabled"] = True
        frozen["rc_astro"]["license_confirmed"] = True
        frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "execution contract")


def test_result_id_marker_ok_and_artifacts_are_required() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        attempt = root / "attempt"
        (attempt / "exports").mkdir(parents=True)
        (attempt / "previews").mkdir()
        executable = root / "PixInsight"
        executable.write_text("x", encoding="utf-8")
        params = {"execution_id": "a" * 32, "frozen_executable": str(executable), "route_fingerprint": "route-1"}
        result = successful_result(params, attempt)
        pixinsight_engine.validate_result(result, params["execution_id"], attempt, route_fingerprint="route-1", executable=executable)
        bad = dict(result, execution_id="b" * 32)
        expect_pipeline_error(lambda: pixinsight_engine.validate_result(bad, params["execution_id"], attempt), "execution_id")
        bad = dict(result, successMarker="WRONG")
        expect_pipeline_error(lambda: pixinsight_engine.validate_result(bad, params["execution_id"], attempt), "successMarker")
        bad = dict(result, ok=False)
        expect_pipeline_error(lambda: pixinsight_engine.validate_result(bad, params["execution_id"], attempt), "PJSR failed")
        bad = json.loads(json.dumps(result))
        bad["execution"]["routeFingerprint"] = "route-2"
        expect_pipeline_error(
            lambda: pixinsight_engine.validate_result(bad, params["execution_id"], attempt, route_fingerprint="route-1"),
            "route fingerprint",
        )


def test_pipeline_manifest_requires_runtime_stages_formats_and_previews() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        attempt = root / "attempt"
        (attempt / "exports").mkdir(parents=True)
        (attempt / "previews").mkdir()
        params = {"execution_id": "a" * 32, "frozen_executable": str(root / "PixInsight"), "route_fingerprint": "route-1"}
        result = successful_result(params, attempt)
        pixinsight_engine.validate_result(result, params["execution_id"], attempt, route_fingerprint="route-1")

        missing_runtime = json.loads(json.dumps(result))
        missing_runtime.pop("pixinsight")
        expect_pipeline_error(
            lambda: pixinsight_engine.validate_result(missing_runtime, params["execution_id"], attempt),
            "runtime tuple",
        )

        missing_stage = json.loads(json.dumps(result))
        missing_stage["stages"].pop()
        expect_pipeline_error(
            lambda: pixinsight_engine.validate_result(missing_stage, params["execution_id"], attempt),
            "stage manifest",
        )

        missing_format = json.loads(json.dumps(result))
        missing_format["exports"]["files"].pop()
        expect_pipeline_error(
            lambda: pixinsight_engine.validate_result(missing_format, params["execution_id"], attempt),
            "formats",
        )

        changed_dimensions = json.loads(json.dumps(result))
        changed_dimensions["exports"]["files"][0]["width"] = 95
        expect_pipeline_error(
            lambda: pixinsight_engine.validate_result(changed_dimensions, params["execution_id"], attempt),
            "dimensions/channels",
        )

        escaped_checkpoint = json.loads(json.dumps(result))
        escaped_checkpoint["stages"][0]["evidence"]["artifacts"][0]["path"] = "/etc/01-abe-linear.xisf"
        expect_pipeline_error(
            lambda: pixinsight_engine.validate_result(escaped_checkpoint, params["execution_id"], attempt),
            "out-of-attempt artifact",
        )

        missing_preview = json.loads(json.dumps(result))
        missing_preview["previews"].pop()
        expect_pipeline_error(
            lambda: pixinsight_engine.validate_result(missing_preview, params["execution_id"], attempt),
            "preview manifest",
        )


def test_only_one_current_attempt_can_be_prepared() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        original_validate = pixinsight_engine._validate_current_executable
        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def blocked_validate(frozen: dict, override: str | None, dry_run: bool) -> Path:
            entered.set()
            if not release.wait(2):
                raise AssertionError("concurrency test timed out")
            return Path(frozen["executable"])

        def first_execute() -> None:
            try:
                pixinsight_engine.execute(run_dir, None, 1, True)
            except BaseException as exc:  # noqa: BLE001 - surface failures from the worker thread.
                errors.append(exc)

        try:
            pixinsight_engine._validate_current_executable = blocked_validate
            worker = threading.Thread(target=first_execute)
            worker.start()
            assert entered.wait(2)
            expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "preparing")
            release.set()
            worker.join(2)
            assert not worker.is_alive()
            assert not errors
        finally:
            release.set()
            pixinsight_engine._validate_current_executable = original_validate


def test_orphaned_running_attempt_can_reconcile_to_needs_review() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        dry = pixinsight_engine.execute(run_dir, None, 1, True)
        attempt_dir = Path(dry["attempt_dir"])
        attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
        attempt["status"] = "running"
        (attempt_dir / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")
        state_path = run_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "running"
        state["current_attempt"] = dry["attempt_id"]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        params = json.loads((attempt_dir / "params.json").read_text(encoding="utf-8"))
        (attempt_dir / "logs" / "result.json").write_text(json.dumps(successful_result(params, attempt_dir)), encoding="utf-8")
        message = pixinsight_engine.reconcile_attempt(run_dir, dry["attempt_id"])
        assert "needs_review" in message
        assert json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["status"] == "needs_review"


def test_orphaned_launching_attempt_can_reconcile_or_recover() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        dry = pixinsight_engine.execute(run_dir, None, 1, True)
        attempt_dir = Path(dry["attempt_dir"])
        attempt_path = attempt_dir / "attempt.json"
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        attempt["status"] = "launching"
        attempt["launcher_pid"] = 999_999_999
        attempt_path.write_text(json.dumps(attempt), encoding="utf-8")
        state_path = run_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "launching"
        state["current_attempt"] = dry["attempt_id"]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        message = pixinsight_engine.reconcile_attempt(run_dir, dry["attempt_id"])
        assert "Recovered orphaned" in message
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "failed"
        assert state["current_attempt"] is None
        retry = pixinsight_engine.execute(run_dir, None, 1, True)
        assert retry["attempt_id"] != dry["attempt_id"]

    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        dry = pixinsight_engine.execute(run_dir, None, 1, True)
        attempt_dir = Path(dry["attempt_dir"])
        attempt_path = attempt_dir / "attempt.json"
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        attempt["status"] = "launching"
        attempt_path.write_text(json.dumps(attempt), encoding="utf-8")
        state_path = run_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "launching"
        state["current_attempt"] = dry["attempt_id"]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        params = json.loads((attempt_dir / "params.json").read_text(encoding="utf-8"))
        (attempt_dir / "logs" / "result.json").write_text(
            json.dumps(successful_result(params, attempt_dir)), encoding="utf-8"
        )
        message = pixinsight_engine.reconcile_attempt(run_dir, dry["attempt_id"])
        assert "needs_review" in message


def test_partial_json_is_retried_until_atomic_result_is_complete() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        original_popen = pixinsight_engine.subprocess.Popen
        original_validate = pixinsight_engine._validate_current_executable

        class PartialPopen:
            def __init__(self, command: list[str], **_: object) -> None:
                attempt_dir = Path(command[1].split("=", 1)[1]).parents[1]
                params = json.loads((attempt_dir / "params.json").read_text(encoding="utf-8"))
                result_path = attempt_dir / "logs" / "result.json"
                result_path.write_text('{"ok":', encoding="utf-8")

                def finish() -> None:
                    time.sleep(0.1)
                    result_path.write_text(json.dumps(successful_result(params, attempt_dir)), encoding="utf-8")

                threading.Thread(target=finish, daemon=True).start()

        try:
            pixinsight_engine.subprocess.Popen = PartialPopen
            pixinsight_engine._validate_current_executable = lambda frozen, override, dry: Path(frozen["executable"])
            result = pixinsight_engine.execute(run_dir, None, 2, False)
        finally:
            pixinsight_engine.subprocess.Popen = original_popen
            pixinsight_engine._validate_current_executable = original_validate
        attempt = json.loads((run_dir / "attempts" / result["attempt_id"] / "attempt.json").read_text(encoding="utf-8"))
        assert attempt["incomplete_json_observed"] is True


def test_timeout_late_result_is_quarantined_before_new_attempt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        original_popen = pixinsight_engine.subprocess.Popen
        original_validate = pixinsight_engine._validate_current_executable
        try:
            pixinsight_engine.subprocess.Popen = lambda *args, **kwargs: object()
            pixinsight_engine._validate_current_executable = lambda frozen, override, dry: Path(frozen["executable"])
            expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 0, False), "quarantined")
        finally:
            pixinsight_engine.subprocess.Popen = original_popen
            pixinsight_engine._validate_current_executable = original_validate
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        timed_out_id = state["attempts"][-1]
        attempt_dir = run_dir / "attempts" / timed_out_id
        attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
        params = json.loads((attempt_dir / "params.json").read_text(encoding="utf-8"))
        (attempt_dir / "logs" / "result.json").write_text(json.dumps(successful_result(params, attempt_dir)), encoding="utf-8")
        retry = pixinsight_engine.execute(run_dir, None, 1, True)
        assert retry["attempt_id"] != timed_out_id
        attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
        assert attempt["late_result"]["quarantined"] is True


def test_runtime_probe_is_separate_and_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        executable = root / "PixInsight"
        executable.write_text("x", encoding="utf-8")
        attempt = root / "probe" / "attempts" / "probe-001"
        (attempt / "logs").mkdir(parents=True)
        (attempt / "scripts").mkdir()
        script_path = attempt / "scripts" / "pixinsight-pipeline.js"
        script_path.write_text("probe script", encoding="utf-8")
        execution_id = "a" * 32
        result_path = attempt / "logs" / "result.json"
        params = {
            "mode": "probe",
            "execution_id": execution_id,
            "result": str(result_path),
            "frozen_executable": str(executable),
            "route_fingerprint": "capability-probe",
        }
        (attempt / "params.json").write_text(json.dumps(params), encoding="utf-8")
        (attempt / "attempt.json").write_text(json.dumps({
            "kind": "probe",
            "status": "complete",
            "execution_id": execution_id,
            "executable": str(executable),
            "params_fingerprint": pixinsight_engine.fingerprint(params),
            "script": str(script_path),
            "script_fingerprint": pixinsight_engine.fingerprint(script_path.read_text(encoding="utf-8")),
            "result": str(result_path),
        }), encoding="utf-8")
        result = {
            "schema_version": 1,
            "mode": "probe",
            "ok": True,
            "execution_id": execution_id,
            "successMarker": pixinsight_engine.PROBE_MARKER,
            "execution": {
                "frozenExecutable": str(executable),
                "routeFingerprint": "capability-probe",
                "params": str(attempt / "params.json"),
            },
            "pixinsight": {
                "versionMajor": 1,
                "versionMinor": 9,
                "versionRelease": 3,
                "versionRevision": 0,
                "versionBuild": 1646,
            },
            "stages": [{"stage": "runtime-capability-probe", "ok": True}],
            "rc_astro": {
                key: {"available": True, "ai_file": pixinsight.RC_ASTRO_MODULES[key]["expected_model"]}
                for key in ("bxt", "sxt", "nxt")
            },
        }
        result_path.write_text(json.dumps(result), encoding="utf-8")
        pixinsight_engine.validate_runtime_probe(result_path, executable)
        result["rc_astro"]["nxt"]["available"] = False
        result_path.write_text(json.dumps(result), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.validate_runtime_probe(result_path, executable), "NXT")
        detached = root / "probe.json"
        detached.write_text(json.dumps(result), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.validate_runtime_probe(detached, executable), "inside a probe attempt")

        params["route_fingerprint"] = "detached-route"
        (attempt / "params.json").write_text(json.dumps(params), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.validate_runtime_probe(result_path, executable), "route")


def test_rc_astro_module_version_and_model_discovery_does_not_set_license() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        executable = root / "PixInsight.app" / "Contents" / "MacOS" / "PixInsight"
        executable.parent.mkdir(parents=True)
        executable.write_text("x", encoding="utf-8")
        module_dir = root / "bin"
        module_dir.mkdir()
        for specification in pixinsight.RC_ASTRO_MODULES.values():
            (module_dir / specification["filename"]).write_bytes(
                b"PIXINSIGHT_MODULE_VERSION_1.2.3.0.eng\x00" + specification["model_token"].encode("ascii")
            )
        discovered = pixinsight.discover_rc_astro(executable)
        assert discovered["installed"] is True
        assert discovered["available"] is False
        assert discovered["license"] == "unconfirmed"
        assert all(item["version"] == "1.2.3" for item in discovered["modules"].values())


def test_explicit_processor_conflict_is_a_preflight_blocker() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / ".astro-processing").mkdir()
        (root / ".astro-processing" / "project.yaml").write_text(
            json.dumps({"processors": {"background_extraction": {"mode": "require", "candidates": ["graxpert"]}}}),
            encoding="utf-8",
        )
        source = fake_xisf(root)
        executable = root / "PixInsight"
        executable.write_text("x", encoding="utf-8")
        unavailable = {"available": False, "installed": False, "maturity": "unavailable", "stages": {}}
        capabilities = {
            "environment": {},
            "siril": unavailable,
            "pixinsight": {"available": True, "installed": True, "executable": str(executable), "version": "test", "platform": "test", "maturity": "experimental", "stages": {stage: "experimental" for stage in ("background_extraction", "deconvolution", "star_separation", "denoise")}, "rc_astro_modules": unavailable},
            "graxpert": {"available": True, "installed": True, "maturity": "experimental", "stages": {"background_extraction": "experimental", "denoise": "experimental"}},
            "starnet": unavailable,
            "setiastro": unavailable,
            "rc_astro": unavailable,
        }
        original = astro_module.discover
        try:
            astro_module.discover = lambda *args, **kwargs: capabilities
            args = astro_module.build_parser().parse_args([
                "preflight", "--input", str(source), "--output", str(root / "out"), "--project", str(root),
                "--backend", "pixinsight", "--input-state", "integrated-linear", "--data-type", "osc-color",
            ])
            payload, _, _, _ = astro_module.preflight_payload(args, root)
        finally:
            astro_module.discover = original
        assert any(item["code"] == "PIXINSIGHT_EXPLICIT_PROCESSOR_CONFLICT" for item in payload["blockers"])


def test_unified_run_dispatches_pixinsight_dry_run() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        assert astro_main(["run", "--run", str(run_dir), "--dry-run"]) == 0


def test_template_contains_boundary_and_authenticity_gates() -> None:
    text = pixinsight_engine.PJSR_TEMPLATE.read_text(encoding="utf-8")
    for required in (
        "sameDimensions", "sameChannels", "bitDepthOK", "wcsPreserved", "orientationOK",
        "ASTRO_PROCESSING_PIXINSIGHT_OK", "ASTRO_PROCESSING_PIXINSIGHT_PROBE_OK", "File.move",
        "01-abe-corrected.png", "01-abe-model.png", "04-starless.png", "04-stars.png",
    ):
        assert required in text


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
