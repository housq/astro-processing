# Astro Processing

Reusable Codex skills and deterministic helpers for astrophotography processing.

## Included skills

- [`astro-processing`](astro-processing/SKILL.md): the single environment-aware user entry. It discovers Siril, PixInsight, GraXpert, StarNet, and RC-Astro capabilities; resolves preferences and support maturity; freezes a confirmed run route; and delegates pixel processing through isolated adapters.

## Installation

### Ask Codex to install it

Send the following prompt to the Agent. The repository is private, so the machine must already have access through GitHub credentials; never paste an access token into the prompt.

```text
Use $skill-installer to install the astro-processing skill from the private GitHub repository housq/astro-processing, path astro-processing, ref main. Install it into the standard Codex skills directory. Use existing GitHub credentials, verify that SKILL.md and agents/openai.yaml were installed, and tell me when it will become available. If the destination already exists or authentication is unavailable, stop and explain the safe update or authentication step instead of overwriting anything. Do not install Siril, PixInsight, GraXpert, StarNet, RC-Astro, models, or licenses as part of installing the skill.
```

The Agent should use Codex's bundled `skill-installer`, which installs to `$CODEX_HOME/skills/astro-processing` or `~/.codex/skills/astro-processing` when `CODEX_HOME` is unset. The skill is available to Codex on the next turn after installation.

### Manual fallback

With authenticated Git access, clone the repository to a temporary directory and copy only the skill folder into the standard skills directory:

```bash
install_root="${CODEX_HOME:-$HOME/.codex}/skills"
temporary_repo="$(mktemp -d)/astro-processing-repo"
git clone --depth 1 --filter=blob:none --sparse git@github.com:housq/astro-processing.git "$temporary_repo"
git -C "$temporary_repo" sparse-checkout set astro-processing
test ! -e "$install_root/astro-processing"
mkdir -p "$install_root"
cp -R "$temporary_repo/astro-processing" "$install_root/astro-processing"
test -f "$install_root/astro-processing/SKILL.md"
test -f "$install_root/astro-processing/agents/openai.yaml"
```

The manual fallback deliberately refuses to overwrite an existing installation. Use Git history or a reviewed update procedure when replacing one.

## Planned expansion

PixInsight is represented by an experimental discovery and planning adapter. PJSR post-integration stages will be added first, followed by full WBPP calibration and integration after forward validation. Other software remains isolated behind adapters rather than becoming competing user-visible skills.

## Data policy

Observation data, calibration frames, generated runs, downloaded applications, AI models, and machine-local caches are not stored in this repository. Each skill keeps scripts, references, safety rules, and reproducibility metadata in source control while large image products remain outside Git.
