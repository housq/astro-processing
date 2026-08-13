# Astro Processing

Reusable Codex skills and deterministic helpers for astrophotography processing.

## Included skills

- [`siril-deep-sky-processing`](siril-deep-sky-processing/SKILL.md): OSC deep-sky and comet calibration, quality filtering, registration, stacking, visual refinement, optional StarNet/GraXpert/RC-Astro processing, and FITS/TIFF/PNG/JPEG export through Siril CLI.

## Planned expansion

The repository is intentionally organized as a collection of independent processing skills. A PixInsight workflow can be added later as a sibling directory without coupling it to the Siril implementation.

## Data policy

Observation data, calibration frames, generated runs, downloaded applications, AI models, and machine-local caches are not stored in this repository. Each skill keeps scripts, references, safety rules, and reproducibility metadata in source control while large image products remain outside Git.
