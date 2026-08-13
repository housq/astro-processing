"""Shared read-only adapter discovery helpers."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Iterable


def executable(candidates: Iterable[str | Path | None]) -> Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def command(name: str) -> str | None:
    return shutil.which(name)


def platform_key() -> str:
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def maturity(validated_platforms: set[str]) -> str:
    return "validated" if platform_key() in validated_platforms else "experimental"
