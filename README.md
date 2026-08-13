# Astro Processing

Reusable Codex skills and deterministic helpers for astrophotography processing.

## Included skills

- [`astro-processing`](astro-processing/SKILL.md): the single environment-aware user entry. It discovers Siril, PixInsight, GraXpert, StarNet, and RC-Astro capabilities; resolves preferences and support maturity; freezes a confirmed run route; and delegates pixel processing through isolated adapters.

## Planned expansion

PixInsight is represented by an experimental discovery and planning adapter. PJSR post-integration stages will be added first, followed by full WBPP calibration and integration after forward validation. Other software remains isolated behind adapters rather than becoming competing user-visible skills.

## Data policy

Observation data, calibration frames, generated runs, downloaded applications, AI models, and machine-local caches are not stored in this repository. Each skill keeps scripts, references, safety rules, and reproducibility metadata in source control while large image products remain outside Git.
