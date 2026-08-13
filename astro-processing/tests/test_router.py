#!/usr/bin/env python3
"""Dependency-free regression tests for config and routing behavior."""

from __future__ import annotations

import tempfile
import os
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from config import DEFAULTS, dump_yaml, load_yaml, resolve_config, write_yaml  # noqa: E402
from router import RoutingError, build_route  # noqa: E402


def capabilities() -> dict:
    return {
        "siril": {
            "available": True,
            "installed": True,
            "maturity": "validated",
            "stages": {
                "background_extraction": "validated",
                "denoise": "validated",
                "deconvolution": "unavailable",
                "star_separation": "unavailable",
            },
        },
        "pixinsight": {"available": False, "installed": False, "maturity": "unavailable", "stages": {}},
        "graxpert": {
            "available": True,
            "installed": True,
            "maturity": "validated",
            "stages": {"background_extraction": "validated", "denoise": "validated"},
        },
        "starnet": {
            "available": True,
            "installed": True,
            "maturity": "validated",
            "stages": {"star_separation": "validated"},
        },
        "rc_astro": {
            "available": False,
            "installed": False,
            "maturity": "unavailable",
            "license": "unknown",
            "stages": {},
        },
    }


def test_balanced_route() -> None:
    route = build_route(DEFAULTS, capabilities())
    assert route["main_backend"]["selected"] == "siril"
    assert route["processors"]["background_extraction"]["selected"] == "graxpert"
    assert route["processors"]["star_separation"]["selected"] == "starnet"
    assert route["processors"]["deconvolution"]["selected"] == "disabled"
    assert route["processors"]["denoise"]["selected"] == "graxpert"


def test_hard_requirement_fails() -> None:
    config = {**DEFAULTS, "processors": {**DEFAULTS["processors"], "star_separation": {"mode": "require", "candidates": ["sxt"]}}}
    try:
        build_route(config, capabilities())
    except RoutingError as exc:
        assert "Required processor sxt" in str(exc)
    else:
        raise AssertionError("Unavailable hard requirement did not fail")


def test_explicit_pixinsight_requirement_is_not_silently_replaced() -> None:
    try:
        build_route(DEFAULTS, capabilities(), "pixinsight")
    except RoutingError as exc:
        assert "not installed" in str(exc)
    else:
        raise AssertionError("Explicit PixInsight requirement silently fell back")


def test_config_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "config.yaml"
        path.write_text(dump_yaml(DEFAULTS), encoding="utf-8")
        loaded = load_yaml(path)
        assert loaded["routing"]["preferred_main_backend"] == "siril"
        assert loaded["processors"]["star_separation"]["candidates"] == ["sxt", "starnet", "disabled"]


def test_runtime_snapshot_roundtrip() -> None:
    snapshot = {
        "route": {"main_backend": {"selected": "siril"}},
        "preflight": {
            "groups": [{"kind": "light", "count": 55}, {"kind": "dark", "count": 20}],
            "warnings": [{"code": "DARK_TEMPERATURE_MISMATCH", "severity": "high"}],
        },
    }
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "run-config.yaml"
        write_yaml(path, snapshot)
        loaded = load_yaml(path)
        assert loaded == snapshot


def test_config_precedence() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config_home = root / "config"
        project = root / "project"
        (config_home / "astro-processing").mkdir(parents=True)
        (project / ".astro-processing").mkdir(parents=True)
        (config_home / "astro-processing/preferences.yaml").write_text("execution:\n  profile: fast\n", encoding="utf-8")
        (project / ".astro-processing/project.yaml").write_text("execution:\n  profile: quality\n", encoding="utf-8")
        (project / ".astro-processing/project.local.yaml").write_text("execution:\n  profile: native-only\n", encoding="utf-8")
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        try:
            resolved, sources, loaded = resolve_config(project, {"execution": {"profile": "balanced"}})
        finally:
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous
        assert resolved["execution"]["profile"] == "balanced"
        assert sources["execution.profile"] == "current-request"
        assert len(loaded) == 3


def main() -> None:
    tests = [
        test_balanced_route,
        test_hard_requirement_fails,
        test_explicit_pixinsight_requirement_is_not_silently_replaced,
        test_config_roundtrip,
        test_runtime_snapshot_roundtrip,
        test_config_precedence,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
