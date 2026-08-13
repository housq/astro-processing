#!/usr/bin/env python3
"""Compatibility launcher for the unified astro-processing Siril engine."""

from __future__ import annotations

import runpy
from pathlib import Path


ENGINE = Path(__file__).resolve().parents[2] / "astro-processing" / "scripts" / "adapters" / "siril_engine.py"

if __name__ == "__main__":
    runpy.run_path(str(ENGINE), run_name="__main__")
