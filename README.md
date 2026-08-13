# Astro Processing

Reusable Agent Skills and deterministic helpers for astrophotography processing.

## Included skills

- [`astro-processing`](astro-processing/SKILL.md): the single environment-aware user entry. It discovers Siril, PixInsight, GraXpert, StarNet, and RC-Astro capabilities; resolves preferences and support maturity; freezes a confirmed run route; and delegates pixel processing through isolated adapters.

## Installation

Send this prompt to a general-purpose Agent:

```text
Install the `astro-processing` Agent Skill from
https://github.com/housq/astro-processing/tree/main/astro-processing.
Detect the correct user-level skill directory and installation method for your
environment, install the complete `astro-processing` folder, validate its
`SKILL.md` and referenced files, then report the install path and whether a
reload or new session is required. If an existing installation would be
overwritten or this skill format is unsupported, stop and explain instead.
Do not install the astronomy applications or models yet.
```

## Planned expansion

PixInsight is represented by an experimental discovery and planning adapter. PJSR post-integration stages will be added first, followed by full WBPP calibration and integration after forward validation. Other software remains isolated behind adapters rather than becoming competing user-visible skills.

## Data policy

Observation data, calibration frames, generated runs, downloaded applications, AI models, and machine-local caches are not stored in this repository. Each skill keeps scripts, references, safety rules, and reproducibility metadata in source control while large image products remain outside Git.
