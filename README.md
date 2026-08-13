# Astro Processing

Reusable Agent Skills and deterministic helpers for astrophotography processing.

## Included skills

- [`astro-processing`](astro-processing/SKILL.md): the single environment-aware user entry. It discovers Siril, PixInsight, GraXpert, StarNet, RC-Astro, and experimental SETI Astro capabilities; resolves preferences and support maturity; freezes a confirmed run route; and delegates pixel processing through isolated adapters and reviewable checkpoints.

## Installation

Send this prompt to a general-purpose Agent:

```text
Install the `astro-processing` Agent Skill from
https://github.com/housq/astro-processing/tree/main/astro-processing.
Install the complete folder in your user-level skills directory, verify
`SKILL.md` and its referenced files, and report the path and reload requirement.
Do not overwrite an existing installation or install astronomy dependencies.
```

## Planned expansion

PixInsight is represented by an experimental discovery and planning adapter. PJSR post-integration stages will be added first, followed by full WBPP calibration and integration after forward validation. Other software remains isolated behind adapters rather than becoming competing user-visible skills.

## Data policy

Observation data, calibration frames, generated runs, downloaded applications, AI models, and machine-local caches are not stored in this repository. Each skill keeps scripts, references, safety rules, and reproducibility metadata in source control while large image products remain outside Git.

## License

This repository's code and documentation are licensed under the [Apache License 2.0](LICENSE). Referenced astronomy applications, plugins, and model files are not distributed here and remain subject to their respective licenses.
