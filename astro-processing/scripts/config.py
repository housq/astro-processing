#!/usr/bin/env python3
"""Configuration loading and YAML serialization for astro-processing."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "routing": {
        "preferred_main_backend": "siril",
        "allow_mixed_main_backends": False,
    },
    "execution": {"profile": "balanced"},
    "processors": {
        "background_extraction": {"mode": "auto", "candidates": []},
        "deconvolution": {"mode": "disabled", "candidates": []},
        "star_separation": {"mode": "prefer", "candidates": ["sxt", "starnet", "disabled"]},
        "denoise": {"mode": "auto", "candidates": []},
    },
    "behavior": {
        "confirm_first_route": True,
        "confirm_before_install": True,
        "confirm_before_license_activation": True,
    },
    "output": {"formats": ["fits", "tif", "png", "jpg"]},
}


class ConfigError(RuntimeError):
    pass


def _scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return {}
    if text in {"null", "~"}:
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if text.startswith("[") or text.startswith("{"):
        try:
            return json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            if text.startswith("[") and text.endswith("]"):
                return [_scalar(item) for item in text[1:-1].split(",") if item.strip()]
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)", text):
        return float(text)
    return text


def load_yaml(path: Path) -> dict[str, Any]:
    """Load the small mapping/list YAML subset used by this project."""
    if not path.is_file():
        return {}
    content = path.read_text(encoding="utf-8")
    stripped = content.lstrip()
    # JSON is a valid YAML 1.2 document. Runtime snapshots use it so nested
    # diagnostic lists remain lossless without adding a PyYAML dependency.
    if stripped.startswith("{"):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON-compatible YAML in {path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ConfigError(f"Expected a mapping at the root of {path}")
        return parsed
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw in enumerate(content.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"Tabs are not supported in {path}:{line_number}")
        indent = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()
        if ":" not in text:
            raise ConfigError(f"Expected key: value in {path}:{line_number}")
        key, value = text.split(":", 1)
        key = key.strip().strip('"\'')
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        parsed = _scalar(value)
        parent[key] = parsed
        if parsed == {}:
            stack.append((indent, parsed))
    return root


def _quote(value: str) -> str:
    if not value or re.search(r"[:#\[\]{},&*!|>'\"%@`\s]", value) or value.lower() in {"true", "false", "null"}:
        return json.dumps(value, ensure_ascii=False)
    return value


def dump_yaml(value: dict[str, Any]) -> str:
    lines: list[str] = []

    def emit(mapping: dict[str, Any], indent: int) -> None:
        prefix = " " * indent
        for key, item in mapping.items():
            if isinstance(item, dict):
                lines.append(f"{prefix}{key}:")
                emit(item, indent + 2)
            elif isinstance(item, list):
                rendered = ", ".join(_quote(str(entry)) for entry in item)
                lines.append(f"{prefix}{key}: [{rendered}]")
            elif isinstance(item, bool):
                lines.append(f"{prefix}{key}: {'true' if item else 'false'}")
            elif item is None:
                lines.append(f"{prefix}{key}: null")
            elif isinstance(item, str):
                lines.append(f"{prefix}{key}: {_quote(item)}")
            else:
                lines.append(f"{prefix}{key}: {item}")

    emit(value, 0)
    return "\n".join(lines) + "\n"


def write_yaml(path: Path, value: dict[str, Any]) -> None:
    """Atomically write lossless JSON-compatible YAML.

    Human-authored preference files may use the compact YAML subset accepted by
    ``load_yaml``. Generated evidence can contain lists of nested mappings, so it
    is serialized as JSON, which is also valid YAML 1.2.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def deep_merge(base: dict[str, Any], overlay: dict[str, Any], source: str, sources: dict[str, str], prefix: str = "") -> None:
    for key, value in overlay.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value, source, sources, dotted)
        else:
            base[key] = deepcopy(value)
            sources[dotted] = source


def find_project_root(start: Path) -> Path:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".astro-processing").exists() or (candidate / ".git").exists():
            return candidate
    return current


def global_preferences_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "astro-processing" / "preferences.yaml"


def resolve_config(project_root: Path, overrides: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    resolved = deepcopy(DEFAULTS)
    sources = {key: "skill-default" for key in _leaf_keys(resolved)}
    loaded: list[str] = []
    paths = [
        (global_preferences_path(), "user-global"),
        (project_root / ".astro-processing" / "project.yaml", "project"),
        (project_root / ".astro-processing" / "project.local.yaml", "project-local"),
    ]
    for path, label in paths:
        if path.is_file():
            deep_merge(resolved, load_yaml(path), label, sources)
            loaded.append(str(path))
    if overrides:
        deep_merge(resolved, overrides, "current-request", sources)
    return resolved, sources, loaded


def _leaf_keys(mapping: dict[str, Any], prefix: str = "") -> list[str]:
    result: list[str] = []
    for key, value in mapping.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.extend(_leaf_keys(value, dotted))
        else:
            result.append(dotted)
    return result
