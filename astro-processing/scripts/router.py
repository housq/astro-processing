#!/usr/bin/env python3
"""Deterministic phase-one routing for astro-processing."""

from __future__ import annotations

from typing import Any


class RoutingError(RuntimeError):
    pass


MAIN_BACKENDS = ("siril", "pixinsight")
PROCESSOR_STAGES = (
    "satellite_removal",
    "background_extraction",
    "deconvolution",
    "detail_restoration",
    "star_separation",
    "denoise",
)
PROCESSOR_DEFAULTS = {
    "balanced": {
        "satellite_removal": ["disabled"],
        "background_extraction": ["graxpert", "main"],
        "deconvolution": ["bxt", "disabled"],
        "detail_restoration": ["disabled"],
        "star_separation": ["sxt", "starnet", "main", "disabled"],
        "denoise": ["nxt", "graxpert", "main"],
    },
    "quality": {
        "satellite_removal": ["disabled"],
        "background_extraction": ["graxpert", "main"],
        "deconvolution": ["bxt", "disabled"],
        "detail_restoration": ["disabled"],
        "star_separation": ["sxt", "starnet", "main", "disabled"],
        "denoise": ["nxt", "graxpert", "main"],
    },
    "fast": {
        "satellite_removal": ["disabled"],
        "background_extraction": ["main"],
        "deconvolution": ["disabled"],
        "detail_restoration": ["disabled"],
        "star_separation": ["disabled"],
        "denoise": ["main"],
    },
    "native-only": {
        "satellite_removal": ["disabled"],
        "background_extraction": ["main"],
        "deconvolution": ["disabled"],
        "detail_restoration": ["disabled"],
        "star_separation": ["main", "disabled"],
        "denoise": ["main"],
    },
}


def _validated(capability: dict[str, Any], stage: str | None = None) -> bool:
    if not capability.get("available"):
        return False
    if stage:
        return capability.get("stages", {}).get(stage) == "validated"
    return capability.get("maturity") == "validated"


def _experimental(capability: dict[str, Any], stage: str | None = None) -> bool:
    if not capability.get("installed"):
        return False
    if stage:
        return capability.get("stages", {}).get(stage) == "experimental"
    return capability.get("maturity") == "experimental"


def resolve_main_backend(config: dict[str, Any], capabilities: dict[str, Any], explicit: str = "auto") -> dict[str, Any]:
    requested = explicit if explicit != "auto" else str(config.get("routing", {}).get("preferred_main_backend", "siril"))
    source = "current-request" if explicit != "auto" else "resolved-preference"
    if requested not in MAIN_BACKENDS:
        raise RoutingError(f"Unknown main backend: {requested}")
    capability = capabilities[requested]
    if explicit != "auto":
        if not capability.get("installed"):
            raise RoutingError(f"Required main backend {requested} is not installed")
        return {
            "selected": requested,
            "mode": "require",
            "source": source,
            "maturity": capability.get("maturity"),
            "requires_confirmation": capability.get("maturity") != "validated",
            "reason": f"User explicitly required {requested}",
        }
    if _validated(capability):
        return {
            "selected": requested,
            "mode": "prefer",
            "source": source,
            "maturity": "validated",
            "requires_confirmation": False,
            "reason": f"Selected validated preferred backend {requested}",
        }
    siril = capabilities["siril"]
    if _validated(siril):
        return {
            "selected": "siril",
            "mode": "auto",
            "source": "validated-fallback",
            "maturity": "validated",
            "fallback_from": requested if requested != "siril" else None,
            "requires_confirmation": requested != "siril",
            "reason": "Selected validated Siril fallback",
        }
    raise RoutingError("No validated main backend is available; explicitly choose an installed experimental backend or resolve the environment")


def _tool_stage(tool: str, stage: str, main: str) -> tuple[str, str]:
    if tool == "main":
        return main, stage
    if tool in {"bxt", "nxt", "sxt"}:
        return "rc_astro", stage
    return tool, stage


def _candidate_available(tool: str, stage: str, main: str, capabilities: dict[str, Any], allow_experimental: bool) -> tuple[bool, str]:
    if tool == "disabled":
        return True, "disabled"
    adapter, adapter_stage = _tool_stage(tool, stage, main)
    capability = capabilities.get(adapter, {})
    if tool in {"bxt", "nxt", "sxt"} and capability.get("license") != "active":
        return False, "license-not-active"
    if _validated(capability, adapter_stage):
        return True, "validated"
    if allow_experimental and _experimental(capability, adapter_stage):
        return True, "experimental"
    return False, capability.get("maturity", "unavailable")


def resolve_processor(
    stage: str,
    config: dict[str, Any],
    capabilities: dict[str, Any],
    main: str,
    profile: str,
) -> dict[str, Any]:
    setting = config.get("processors", {}).get(stage, {})
    mode = setting.get("mode", "auto")
    configured = list(setting.get("candidates") or [])
    if mode not in {"require", "prefer", "auto", "disabled"}:
        raise RoutingError(f"processors.{stage}.mode must be require, prefer, auto, or disabled")
    if mode == "disabled":
        return {"selected": "disabled", "mode": mode, "maturity": "disabled", "fallbacks": [], "reason": "Stage disabled by configuration"}
    candidates = configured or list(PROCESSOR_DEFAULTS[profile][stage])
    if mode == "require" and not configured:
        raise RoutingError(f"processors.{stage}.candidates is required when mode=require")
    allow_experimental = profile == "quality" or mode == "require"
    rejected: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates):
        available, state = _candidate_available(candidate, stage, main, capabilities, allow_experimental)
        if not available:
            rejected.append({"candidate": candidate, "reason": state})
            if mode == "require" and index == 0:
                raise RoutingError(f"Required processor {candidate} for {stage} is unavailable ({state})")
            continue
        fallbacks = []
        for fallback in candidates[index + 1 :]:
            fallback_available, _ = _candidate_available(fallback, stage, main, capabilities, False)
            if fallback_available:
                fallbacks.append(fallback)
        return {
            "selected": candidate,
            "mode": mode,
            "maturity": state,
            "fallbacks": fallbacks,
            "rejected_before_selection": rejected,
            "requires_confirmation": state == "experimental",
            "reason": f"Selected first {'configured' if configured else profile} candidate that passed capability checks",
        }
    if mode == "require":
        raise RoutingError(f"No usable required processor for {stage}")
    return {
        "selected": "disabled",
        "mode": mode,
        "maturity": "degraded",
        "fallbacks": [],
        "rejected_before_selection": rejected,
        "requires_confirmation": True,
        "reason": "No candidate passed capability checks; stage will be skipped",
    }


def build_route(config: dict[str, Any], capabilities: dict[str, Any], explicit_backend: str = "auto") -> dict[str, Any]:
    profile = str(config.get("execution", {}).get("profile", "balanced"))
    if profile not in PROCESSOR_DEFAULTS:
        raise RoutingError(f"Unknown execution profile: {profile}")
    main = resolve_main_backend(config, capabilities, explicit_backend)
    processors = {
        stage: resolve_processor(stage, config, capabilities, main["selected"], profile)
        for stage in PROCESSOR_STAGES
    }
    reasons = []
    if main.get("requires_confirmation"):
        reasons.append(f"main backend {main['selected']} is {main['maturity']} or uses a fallback")
    for stage, decision in processors.items():
        if decision.get("requires_confirmation"):
            reasons.append(f"{stage} uses {decision['selected']} ({decision['maturity']})")
    return {
        "execution_profile": profile,
        "main_backend": main,
        "processors": processors,
        "confirmation_reasons": reasons,
    }
