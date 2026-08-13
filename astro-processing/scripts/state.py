#!/usr/bin/env python3
"""Run snapshots and route fingerprints for astro-processing."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from config import write_yaml


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def write_run_snapshot(
    run_dir: Path,
    input_dir: Path,
    project_root: Path,
    config: dict[str, Any],
    sources: dict[str, str],
    loaded_files: list[str],
    capabilities: dict[str, Any],
    route: dict[str, Any],
    preflight: dict[str, Any],
    data_profile: str,
    support_level: str,
) -> Path:
    compact_capabilities = {
        name: {
            key: value
            for key, value in capability.items()
            if key in {"available", "installed", "executable", "version", "maturity", "license", "platform", "stages"}
        }
        for name, capability in capabilities.items()
        if isinstance(capability, dict) and name not in {"environment"}
    }
    snapshot = {
        "schema_version": 1,
        "created_at": now_utc(),
        "input": str(input_dir.resolve()),
        "run": str(run_dir.resolve()),
        "project_root": str(project_root),
        "data_profile": data_profile,
        "support_level": support_level,
        "resolved_config": config,
        "config_sources": sources,
        "loaded_config_files": loaded_files,
        "capabilities": compact_capabilities,
        "resolved_routing": route,
        "preflight": preflight,
        "route_fingerprint": fingerprint({"route": route, "data_profile": data_profile, "support_level": support_level}),
        "confirmation": {"status": "confirmed", "scope": "initial-route-and-recorded-fallbacks"},
    }
    path = run_dir / "run-config.yaml"
    write_yaml(path, snapshot)
    marker = {
        "schema_version": 1,
        "created_at": snapshot["created_at"],
        "run_dir": str(run_dir.resolve()),
        "main_backend": route["main_backend"]["selected"],
        "run_config": str(path),
        "route_fingerprint": snapshot["route_fingerprint"],
    }
    (run_dir / ".astro-run.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
