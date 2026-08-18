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


def successful_result(params: dict, attempt_dir: Path, marker: str = pixinsight_engine.SUCCESS_MARKER) -> dict:
    export = attempt_dir / "exports" / "final.xisf"
    preview = attempt_dir / "previews" / "review.png"
    export.write_bytes(b"xisf-evidence")
    preview.write_bytes(b"png-evidence")
    return {
        "schema_version": 1,
        "ok": True,
        "execution_id": params["execution_id"],
        "successMarker": marker,
        "execution": {
            "frozenExecutable": params.get("frozen_executable"),
            "routeFingerprint": params.get("route_fingerprint"),
        },
        "exports": {"files": [{"path": str(export)}]},
        "previews": [str(preview)],
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
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "needs visual")
        pixinsight_engine.review_attempt(run_dir, result["attempt_id"], "accept", "visual checks passed", [])
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state["status"] == "complete"
        assert state["selected_attempt"] == result["attempt_id"]


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
        expect_pipeline_error(lambda: pixinsight_engine.execute(run_dir, None, 1, True), "route has been modified")


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


def test_orphaned_running_attempt_can_reconcile_to_needs_review() -> None:
    with tempfile.TemporaryDirectory() as temp:
        run_dir = frozen_run(Path(temp))
        dry = pixinsight_engine.execute(run_dir, None, 1, True)
        attempt_dir = Path(dry["attempt_dir"])
        attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
        attempt["status"] = "running"
        (attempt_dir / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")
        params = json.loads((attempt_dir / "params.json").read_text(encoding="utf-8"))
        (attempt_dir / "logs" / "result.json").write_text(json.dumps(successful_result(params, attempt_dir)), encoding="utf-8")
        message = pixinsight_engine.reconcile_attempt(run_dir, dry["attempt_id"])
        assert "needs_review" in message
        assert json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["status"] == "needs_review"


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
        result = {
            "ok": True,
            "execution_id": "a" * 32,
            "successMarker": pixinsight_engine.PROBE_MARKER,
            "execution": {"frozenExecutable": str(executable)},
            "rc_astro": {
                key: {"available": True, "ai_file": pixinsight.RC_ASTRO_MODULES[key]["expected_model"]}
                for key in ("bxt", "sxt", "nxt")
            },
        }
        path = root / "probe.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        pixinsight_engine.validate_runtime_probe(path, executable)
        result["rc_astro"]["nxt"]["available"] = False
        path.write_text(json.dumps(result), encoding="utf-8")
        expect_pipeline_error(lambda: pixinsight_engine.validate_runtime_probe(path, executable), "NXT")


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
