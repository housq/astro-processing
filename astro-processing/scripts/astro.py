#!/usr/bin/env python3
"""Unified environment-aware astrophotography processing orchestrator."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from adapters import graxpert, pixinsight, rc_astro, setiastro, siril, starnet  # noqa: E402
from adapters import pixinsight_engine, siril_engine  # noqa: E402
from config import ConfigError, find_project_root, resolve_config  # noqa: E402
from external_executor import ExternalExecutionError, REVIEW_VERDICTS, ROUTED_STAGES, review_stage, run_stage  # noqa: E402
from router import RoutingError, build_route  # noqa: E402
from state import write_run_snapshot  # noqa: E402


VERSION = "0.2.0"
PASSTHROUGH = {"run", "resume", "postprocess", "comet", "select", "report", "cleanup", "review-pixinsight", "reconcile-pixinsight", "probe-pixinsight", "smoke-pixinsight"}


class AstroError(RuntimeError):
    pass


def package_managers() -> list[dict[str, str]]:
    managers = []
    for name in ("brew", "apt-get", "dnf", "pacman", "zypper"):
        path = shutil.which(name)
        if path:
            managers.append({"name": name, "executable": str(Path(path).resolve())})
    return managers


def discover(project_root: Path, explicit_siril: str | None = None, explicit_pixinsight: str | None = None) -> dict[str, Any]:
    return {
        "environment": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "package_managers": package_managers(),
        },
        "siril": siril.discover(explicit_siril),
        "pixinsight": pixinsight.discover(explicit_pixinsight),
        "graxpert": graxpert.discover(project_root),
        "starnet": starnet.discover(),
        "setiastro": setiastro.discover(),
        "rc_astro": rc_astro.discover(),
    }


def catalog_network() -> dict[str, Any]:
    host = "tapvizier.u-strasbg.fr"
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        return {"available": True, "host": host, "addresses": addresses[:4], "maturity": "observed"}
    except OSError as exc:
        return {"available": False, "host": host, "maturity": "unavailable", "error": str(exc)}


def data_profile(plan: dict[str, Any], moving_target: bool) -> tuple[str, str]:
    bayers = [group.get("reference_metadata", {}).get("bayer") for group in plan.get("groups", [])]
    if bayers and all(bayers):
        return ("osc-comet" if moving_target else "osc-deep-sky"), "validated"
    return ("adaptive-mono-or-unknown"), "adaptive"


def make_overrides(args: argparse.Namespace) -> dict[str, Any]:
    value: dict[str, Any] = {}
    if getattr(args, "profile", None):
        value.setdefault("execution", {})["profile"] = args.profile
    return value


def _pixinsight_preflight(
    args: argparse.Namespace,
    project_root: Path,
    config: dict[str, Any],
    sources: dict[str, str],
    loaded: list[str],
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    profile, blockers = pixinsight_engine.inspect_declared_input(args.input, args.input_state, args.data_type)
    rc_enabled = bool(args.rc_astro)
    rc_discovery = capabilities["pixinsight"].get("rc_astro_modules", {})
    runtime_probe = None
    probe_ok = False
    if args.pixinsight_probe:
        try:
            runtime_probe = pixinsight_engine.validate_runtime_probe(
                args.pixinsight_probe, Path(capabilities["pixinsight"].get("executable", ""))
            )
            probe_pi = runtime_probe.get("pixinsight", {})
            probe_version = ".".join(str(probe_pi.get(field)) for field in ("versionMajor", "versionMinor", "versionRelease"))
            discovered_version = str(capabilities["pixinsight"].get("version", "unknown"))
            if probe_version != discovered_version:
                raise pixinsight_engine.PipelineError(
                    f"PixInsight runtime probe version {probe_version} does not match discovery {discovered_version}"
                )
            probe_ok = True
        except pixinsight_engine.PipelineError as exc:
            blockers.append({"code": "PIXINSIGHT_PROBE_INVALID", "message": str(exc)})
    capabilities["rc_astro"] = {
        **rc_discovery,
        "available": bool(rc_discovery.get("installed") and probe_ok and args.confirm_rc_astro_license),
        "license": "active" if args.confirm_rc_astro_license else "unconfirmed",
        "runtime_probe": "passed" if probe_ok else "missing-or-invalid",
    }
    if rc_enabled:
        if not rc_discovery.get("installed"):
            blockers.append({"code": "RC_ASTRO_MODULES_UNAVAILABLE", "message": "BXT/SXT/NXT module, version, and bundled-model discovery did not all pass"})
        if not probe_ok:
            blockers.append({"code": "RC_ASTRO_RUNTIME_PROBE_REQUIRED", "message": "Run probe-pixinsight and pass its authenticated result with --pixinsight-probe"})
        if not args.confirm_rc_astro_license:
            blockers.append({"code": "RC_ASTRO_LICENSE_UNCONFIRMED", "message": "RC-Astro execution requires separate --confirm-rc-astro-license; no license data is stored"})
    route = build_route(config, capabilities, "pixinsight")
    desired = {
        "background_extraction": "pixinsight",
        "deconvolution": "bxt" if rc_enabled else "disabled",
        "star_separation": "sxt" if rc_enabled else "disabled",
        "denoise": "nxt" if rc_enabled else "pixinsight",
    }
    for stage, selected in desired.items():
        mode_source = sources.get(f"processors.{stage}.mode", "skill-default")
        candidates_source = sources.get(f"processors.{stage}.candidates", "skill-default")
        explicitly_configured = mode_source != "skill-default" or candidates_source != "skill-default"
        resolved = route["processors"][stage].get("selected")
        normalized = "pixinsight" if resolved == "main" else resolved
        if explicitly_configured and normalized != selected:
            blockers.append({
                "code": "PIXINSIGHT_EXPLICIT_PROCESSOR_CONFLICT",
                "message": f"Explicit {stage} processor resolved to {resolved}; consolidated PixInsight route requires {selected}. No silent override was applied.",
            })
            continue
        route["processors"][stage] = {
            "selected": selected,
            "mode": "require" if selected != "disabled" else "disabled",
            "source": "current-request" if rc_enabled and stage in {"deconvolution", "star_separation", "denoise"} else "pixinsight-adapter-default",
            "maturity": capabilities["rc_astro"].get("stages", {}).get(stage, "experimental") if selected in {"bxt", "sxt", "nxt"} else (
                capabilities["pixinsight"].get("stages", {}).get(stage, "experimental") if selected != "disabled" else "disabled"
            ),
            "fallbacks": [],
            "requires_confirmation": selected != "disabled",
            "reason": "Exact consolidated PJSR route; explicit conflicting processor settings are blockers",
        }
    for stage in ("satellite_removal", "detail_restoration"):
        if route["processors"][stage].get("selected") != "disabled":
            blockers.append({"code": "PIXINSIGHT_STAGE_NOT_IMPLEMENTED", "message": f"Frozen processor {stage}={route['processors'][stage]['selected']} is not implemented by this PJSR path"})
    route["confirmation_reasons"] = []
    if route["main_backend"].get("requires_confirmation"):
        route["confirmation_reasons"].append("PixInsight main backend is experimental on this exact validation matrix")
    for stage, decision in route["processors"].items():
        if decision.get("requires_confirmation"):
            route["confirmation_reasons"].append(f"{stage} uses {decision['selected']} ({decision['maturity']})")
    output = args.output.expanduser().resolve() if args.output else pixinsight_engine.default_output(args.input)
    output.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output).free
    estimated = max(profile["bytes"] * 12, 2 * 2**30)
    reserve = max(5 * 2**30, int(estimated * 0.1))
    if free < estimated + reserve:
        blockers.append({"code": "INSUFFICIENT_DISK", "message": f"Need {estimated + reserve} bytes including reserve; only {free} available"})
    degradations = [
        {"code": "PIXINSIGHT_RUNNING_GUI_REQUIRED", "message": "Execution uses --execute IPC and requires a running PixInsight GUI; it is not headless"},
        {"code": "CLASSIC_COLOR_NONPHOTOMETRIC", "message": "SPCC is skipped; classic BackgroundNeutralization + ColorCalibration is non-photometric"},
        {"code": "IMAGE_SOLVER_NOT_AUTOMATED", "message": "ImageSolver remains GUI-assisted/experimental and is not part of this adapter run"},
        {"code": "FITS_CROSS_SOFTWARE_EXPERIMENTAL", "message": "32-bit FITS save/reopen checks are implemented; an external-software round trip is not validated"},
    ]
    return {
        "schema_version": 1,
        "input": str(args.input.expanduser().resolve()), "output": str(output), "project_root": str(project_root),
        "data_profile": "post-integration-osc-color", "support_level": capabilities["pixinsight"].get("maturity", "experimental"),
        "classification": {"integrated_linear_osc": 1}, "groups": [], "declared_input": profile,
        "route": route, "resources": {"estimated_bytes": estimated, "reserve_bytes": reserve, "free_bytes": free},
        "blockers": blockers, "degradations": degradations, "requires_confirmation": True,
        "network_catalog_access": {"available": None, "maturity": "not-required", "reason": "SPCC skipped"},
        "accepted_warnings": [], "loaded_config_files": loaded, "capabilities": capabilities,
        "pixinsight_plan": {
            "rc_astro": rc_enabled,
            "license_confirmed": bool(args.confirm_rc_astro_license),
            "runtime_probe": runtime_probe,
            "input_state": args.input_state,
            "data_type": args.data_type,
        },
    }


def preflight_payload(args: argparse.Namespace, project_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], list[str]]:
    config, sources, loaded = resolve_config(project_root, make_overrides(args))
    capabilities = discover(project_root, getattr(args, "siril", None), getattr(args, "pixinsight", None))
    if args.backend == "pixinsight":
        return _pixinsight_preflight(args, project_root, config, sources, loaded, capabilities), config, sources, loaded
    files, plan = siril.inspect_input(args.input, args.dark_temp_tolerance)
    profile, support = data_profile(plan, args.moving_target)
    route = build_route(config, capabilities, args.backend)
    output = args.output.expanduser().resolve() if args.output else siril_engine.default_output(args.input)
    output.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output).free
    estimated = siril.estimate_bytes(plan)
    reserve = max(5 * 2**30, int(estimated * 0.1))
    blockers = []
    degradations = []
    accepted_warnings = set(getattr(args, "accept_warning", []) or [])
    for group in plan["groups"]:
        for warning in group.get("warnings", []):
            item = {"group": group["id"], **warning}
            if warning.get("code") in accepted_warnings:
                item["accepted_by_user"] = True
                degradations.append(item)
            else:
                (blockers if warning.get("severity") == "high" else degradations).append(item)
    if free < estimated + reserve:
        blockers.append({"code": "INSUFFICIENT_DISK", "message": f"Need {estimated + reserve} bytes including reserve; only {free} available"})
    if support == "adaptive":
        degradations.append({"code": "ADAPTIVE_DATA_PROFILE", "message": "Input is outside the validated OSC profile; review grouping and channel mapping before execution"})
    network = catalog_network() if not getattr(args, "skip_network_check", False) else {"available": None, "maturity": "not-checked"}
    if network.get("available") is False:
        degradations.append({"code": "CATALOG_NETWORK_UNAVAILABLE", "message": "VizieR DNS check did not succeed; online plate solving/PCC must not be claimed unless a later run proves success"})
    elif network.get("available") is None:
        degradations.append({"code": "CATALOG_NETWORK_NOT_CHECKED", "message": "Catalog access was not checked; online plate solving/PCC must prove connectivity before claiming success"})
    first_confirmation = bool(config.get("behavior", {}).get("confirm_first_route", True))
    requires_confirmation = first_confirmation or bool(route["confirmation_reasons"]) or bool(blockers) or bool(degradations)
    payload = {
        "schema_version": 1,
        "input": str(args.input.expanduser().resolve()),
        "output": str(output),
        "project_root": str(project_root),
        "data_profile": profile,
        "support_level": support,
        "classification": plan["counts"],
        "groups": [
            {"id": group["id"], "target": group["target"], "lights": len(group["lights"]), "warnings": group.get("warnings", [])}
            for group in plan["groups"]
        ],
        "route": route,
        "resources": {"estimated_bytes": estimated, "reserve_bytes": reserve, "free_bytes": free},
        "blockers": blockers,
        "degradations": degradations,
        "requires_confirmation": requires_confirmation,
        "network_catalog_access": network,
        "accepted_warnings": sorted(accepted_warnings),
        "loaded_config_files": loaded,
        "capabilities": capabilities,
        "_plan": plan,
        "_files": files,
    }
    return payload, config, sources, loaded


def human_bytes(value: int) -> str:
    return siril_engine.human_bytes(value)


def print_preflight(payload: dict[str, Any]) -> None:
    route = payload["route"]
    processors = ", ".join(f"{stage}={decision['selected']}" for stage, decision in route["processors"].items())
    resources = payload["resources"]
    print(f"Data profile: {payload['data_profile']} [{payload['support_level']}]")
    print(f"Main backend: {route['main_backend']['selected']} [{route['main_backend']['maturity']}]")
    print(f"Processors: {processors}")
    print(f"Disk: estimated {human_bytes(resources['estimated_bytes'])} + reserve {human_bytes(resources['reserve_bytes'])}; free {human_bytes(resources['free_bytes'])}")
    for item in payload["blockers"]:
        print(f"BLOCKER {item['code']}: {item['message']}")
    for item in payload["degradations"]:
        print(f"DEGRADATION {item['code']}: {item['message']}")
    if payload["requires_confirmation"]:
        print("Initial route confirmation is required before creating the run.")


def add_route_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="start directory for project configuration discovery")
    parser.add_argument("--backend", choices=("auto", "siril", "pixinsight"), default="auto")
    parser.add_argument("--profile", choices=("balanced", "quality", "fast", "native-only"))
    parser.add_argument("--moving-target", action="store_true")
    parser.add_argument("--dark-temp-tolerance", type=float, default=5.0)
    parser.add_argument("--siril")
    parser.add_argument("--pixinsight", help="explicit PixInsight executable")
    parser.add_argument("--pixinsight-probe", type=Path, help="authenticated result JSON from probe-pixinsight")
    parser.add_argument("--input-state", choices=("unknown", "integrated-linear"), default="unknown")
    parser.add_argument("--data-type", choices=("unknown", "osc-color", "mono", "lrgb", "narrowband"), default="unknown")
    parser.add_argument("--rc-astro", action="store_true", help="select the licensed PixInsight BXT/SXT/NXT branch")
    parser.add_argument("--confirm-rc-astro-license", action="store_true", help="confirm active local RC-Astro licenses without recording license data")
    parser.add_argument("--accept-warning", action="append", default=[], help="accept one displayed warning code for this run snapshot")
    parser.add_argument("--skip-network-check", action="store_true", help="record catalog network as unchecked; never treat this as online calibration success")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="discover main backends, optional processors, package managers, versions, and maturity")
    doctor.add_argument("--project", type=Path, default=Path.cwd())
    doctor.add_argument("--siril")
    doctor.add_argument("--pixinsight")
    doctor.add_argument("--json", type=Path)
    inspect = sub.add_parser("inspect", help="classify inputs using the validated Siril OSC scanner")
    inspect.add_argument("--input", type=Path, required=True)
    inspect.add_argument("--dark-temp-tolerance", type=float, default=5.0)
    inspect.add_argument("--json", type=Path)
    preflight = sub.add_parser("preflight", help="resolve environment, route, data profile, risks, and resources before execution")
    add_route_arguments(preflight)
    preflight.add_argument("--json", type=Path)
    plan = sub.add_parser("plan", help="create a Siril-backed run after the initial route has been confirmed")
    add_route_arguments(plan)
    plan.add_argument("--run-id")
    plan.add_argument("--confirm-route", action="store_true", help="confirm the displayed initial route and recorded fallbacks")
    plan.add_argument("--json", type=Path)
    stage = sub.add_parser("stage", help="execute one frozen-route processor as a reviewable checkpoint attempt")
    stage.add_argument("--run", type=Path, required=True)
    stage.add_argument("--group", required=True)
    stage.add_argument("--stage", choices=ROUTED_STAGES, required=True)
    stage.add_argument("--processor", help="selected processor or a frozen fallback; SETI A/B use requires --ab-candidate")
    stage.add_argument("--input", type=Path, help="explicit source checkpoint; otherwise resolve the latest accepted checkpoint")
    stage.add_argument("--params", type=Path, help="JSON mapping merged over conservative adapter defaults")
    stage.add_argument("--reference", type=Path, action="append", default=[])
    stage.add_argument("--reference-notes", default="")
    stage.add_argument("--ab-candidate", action="store_true", help="run SETI denoise/starless only as a non-promotable A/B candidate")
    stage.add_argument("--linear", action=argparse.BooleanOptionalAction, default=True)
    stage.add_argument("--siril")
    stage.add_argument("--dry-run", action="store_true")
    review = sub.add_parser("review-stage", help="record visual findings and accept or reject a processor attempt")
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--group", required=True)
    review.add_argument("--attempt", required=True)
    review.add_argument("--verdict", choices=REVIEW_VERDICTS, required=True)
    review.add_argument("--notes", default="")
    review.add_argument("--issue", action="append", default=[])
    review.add_argument("--reference", type=Path, action="append", default=[], help="additional reference supplied during review")
    return parser


def _passthrough_backend(arguments: list[str]) -> int:
    if arguments[0] in {"probe-pixinsight", "smoke-pixinsight"}:
        return pixinsight_engine.run(arguments)
    if arguments[0] not in {"run", "review-pixinsight", "reconcile-pixinsight"}:
        return siril.run(arguments)
    try:
        index = arguments.index("--run")
        run_dir = Path(arguments[index + 1]).expanduser().resolve()
    except (ValueError, IndexError):
        return siril.run(arguments)
    marker = run_dir / ".astro-run.json"
    if marker.is_file():
        try:
            backend = json.loads(marker.read_text(encoding="utf-8")).get("main_backend")
        except (OSError, json.JSONDecodeError) as exc:
            raise AstroError(f"Invalid unified run marker {marker}: {exc}") from exc
        if backend == "pixinsight":
            return pixinsight_engine.run(arguments)
    return siril.run(arguments)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in PASSTHROUGH:
        try:
            return _passthrough_backend(arguments)
        except (AstroError, pixinsight_engine.PipelineError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "doctor":
            project = find_project_root(args.project)
            result = discover(project, args.siril, args.pixinsight)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            if args.json:
                args.json.expanduser().resolve().write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif args.command == "inspect":
            files, plan = siril.inspect_input(args.input, args.dark_temp_tolerance)
            print(siril.print_inspection(args.input, files, plan))
            if args.json:
                args.json.expanduser().resolve().write_text(json.dumps({"files": files, "plan": plan}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif args.command in {"preflight", "plan"}:
            project = find_project_root(args.project)
            payload, config, sources, loaded = preflight_payload(args, project)
            print_preflight(payload)
            serializable = {key: value for key, value in payload.items() if not key.startswith("_")}
            if args.json:
                args.json.expanduser().resolve().write_text(json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            if args.command == "preflight" and payload["blockers"]:
                return 2
            if args.command == "plan":
                if payload["blockers"]:
                    raise AstroError("Preflight has blockers; resolve them before creating a run")
                if not args.confirm_route:
                    raise AstroError("Initial route has not been confirmed; review preflight and re-run with --confirm-route")
                main_backend = payload["route"]["main_backend"]["selected"]
                output = Path(payload["output"])
                if main_backend == "pixinsight":
                    run_dir = pixinsight_engine.create_run(args.input, output, args.run_id)
                else:
                    run_dir = siril_engine.create_run(args.input, output, args.run_id, args.dark_temp_tolerance)
                snapshot = write_run_snapshot(
                    run_dir, args.input, project, config, sources, loaded, payload["capabilities"], payload["route"], serializable,
                    payload["data_profile"], payload["support_level"],
                )
                if main_backend == "pixinsight":
                    pixinsight_engine.freeze_run(run_dir)
                print(f"Run: {run_dir}")
                print(f"Frozen route: {snapshot}")
        elif args.command == "stage":
            print(run_stage(
                args.run, args.group, args.stage, args.processor, args.input, args.params,
                args.reference, args.reference_notes, args.ab_candidate, args.linear, args.siril, args.dry_run,
            ))
        elif args.command == "review-stage":
            print(review_stage(
                args.run, args.group, args.attempt, args.verdict, args.notes, args.issue, args.reference
            ))
        return 0
    except (
        AstroError, ConfigError, RoutingError, ExternalExecutionError,
        siril_engine.PipelineError, pixinsight_engine.PipelineError, OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
