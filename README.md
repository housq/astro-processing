# Astro Processing

Reusable Agent Skills and deterministic helpers for astrophotography processing.

## Included skills

- [`astro-processing`](astro-processing/SKILL.md): the single environment-aware user entry. It discovers Siril, PixInsight, GraXpert, StarNet, and RC-Astro capabilities; resolves preferences and support maturity; freezes a confirmed run route; and delegates pixel processing through isolated adapters.

## Installation

### Ask a general-purpose Agent to install it

Send the following prompt to any Agent that can access GitHub and manage filesystem-based skills. It deliberately does not assume Codex, a particular installer, or a fixed skills directory.

```text
Install the `astro-processing` Agent Skill from the private GitHub repository
https://github.com/housq/astro-processing, branch `main`, source subdirectory
`astro-processing`.

Before changing anything:
1. Inspect your own product documentation, configuration, and existing installed
   skills to determine whether you support `SKILL.md`-based skills and identify
   the correct user-scoped skills directory. Do not assume a Codex-specific
   installer or path.
2. Check whether that destination already contains `astro-processing`. If it
   does, do not overwrite it. Report the installed and source revisions or
   differences, then ask me before performing a safe backup and update.
3. Use existing GitHub authentication available on this machine (for example an
   authenticated GitHub integration, `gh`, Git credential helper, or SSH). Never
   ask me to paste a token or private key into chat. If access is unavailable,
   stop and tell me which normal authentication step is required.

If the platform supports this skill format and the destination is free:
1. Fetch the repository at `main` into a temporary location, using sparse
   checkout or an authenticated download when practical.
2. Install only the repository's `astro-processing` directory into the detected
   user-scoped skills directory, preserving `SKILL.md`, `agents/`, `scripts/`,
   and `references/` exactly. Do not install the repository root as the skill.
3. Verify that the installed `SKILL.md` frontmatter names the skill
   `astro-processing`, that `agents/openai.yaml` exists, and that all relative
   files referenced by `SKILL.md` are present. Run your platform's skill
   validator if one exists.
4. Activate or reload skills using the normal mechanism for your platform. Tell
   me whether the skill is usable immediately or needs a new turn/session.
5. Report the detected Agent product, install destination, source commit hash,
   validation result, and activation status.

Installing the skill itself must not install or modify Siril, PixInsight,
GraXpert, StarNet, RC-Astro, model files, licenses, package managers, or system
packages. Those are separate actions that require later environment checks and
my explicit approval. If your platform cannot install `SKILL.md`-based skills,
do not claim success; explain the incompatibility and the least invasive way to
use this repository as task instructions instead.
```

The repository is private, so the Agent still needs GitHub access granted through its environment. The prompt keeps authentication, overwrite, dependency installation, and platform compatibility as explicit safety boundaries.

## Planned expansion

PixInsight is represented by an experimental discovery and planning adapter. PJSR post-integration stages will be added first, followed by full WBPP calibration and integration after forward validation. Other software remains isolated behind adapters rather than becoming competing user-visible skills.

## Data policy

Observation data, calibration frames, generated runs, downloaded applications, AI models, and machine-local caches are not stored in this repository. Each skill keeps scripts, references, safety rules, and reproducibility metadata in source control while large image products remain outside Git.
