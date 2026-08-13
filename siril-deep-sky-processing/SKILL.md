---
name: siril-deep-sky-processing
description: Compatibility entry for existing Siril color-camera deep-sky and comet workflows. Use when a request explicitly invokes the former Siril-only skill name; route all new environment selection, Siril/PixInsight choice, optional GraXpert/StarNet/RC-Astro processing, preflight, and execution through the unified astro-processing skill.
---

# Siril Deep-Sky Processing Compatibility

Use [the unified skill](../astro-processing/SKILL.md) for every new workflow. Preserve this entry only so existing prompts and automation that invoke `$siril-deep-sky-processing` continue to work.

Resolve the unified CLI as:

```bash
ASTRO=/absolute/path/to/astro-processing/scripts/astro.py
```

Run unified `doctor`, `inspect`, `preflight`, and `plan` before processing. The compatibility launcher at `scripts/astro_pipeline.py` forwards former Siril CLI commands to the single Siril engine maintained inside `astro-processing`; do not fork or reimplement it here.
