---
name: astro-processing
description: Inspect, calibrate, clean, register, integrate, visually refine, and export astrophotography through one environment-aware workflow that routes between Siril and PixInsight main backends and optional RC-Astro, GraXpert, and StarNet processors. Use for color-camera or OSC deep-sky and comet data; for selecting astronomy software from user requirements, preferences, installed versions, licenses, models, platform maturity, and resource limits; for resumable FITS/XISF processing with visual feedback; or for adaptive planning of unvalidated mono/LRGB/narrowband data.
---

# Astro Processing

Use one user-visible workflow while keeping each software implementation isolated behind an adapter. Treat Siril and PixInsight as mutually exclusive main backends for a run unless the user explicitly approves a mixed-backend plan. Treat GraXpert, StarNet, and licensed RC-Astro BXT/NXT/SXT as optional stage processors.

## Preserve the operating contract

- Never modify, rename, move, or delete source data.
- Treat explicit software choices as hard requirements. Treat “prefer” or “best if available” as soft preferences.
- Keep one main backend per run. Require new confirmation before switching it.
- Use a processor only when its executable, version, model/weights, license when applicable, platform maturity, and stage capability pass preflight.
- Run a complete environment and data preflight before expensive work. On the first run or after a material route change, present one consolidated summary and wait for one confirmation.
- After confirmation, adjust parameters and use only recorded fallbacks autonomously. Ask again only for installation, paid activation, main-backend changes, material budget overruns, abandoning a hard requirement, destructive cleanup, or a purely aesthetic tie.
- A zero exit code is insufficient. Require data-integrity, technical, and visual acceptance.

## Load only the relevant references

- Read [routing-and-config.md](references/routing-and-config.md) when resolving preferences, confirmations, support levels, or fallbacks.
- Read [stage-contract.md](references/stage-contract.md) when planning a new backend, crossing software boundaries, or changing stage order.
- Read [pixinsight.md](references/pixinsight.md) before planning or executing PixInsight/PJSR work.
- Read [Siril CLI](references/siril/siril-cli.md) before changing Siril commands or diagnosing Siril failures.
- Read [Siril calibration policy](references/siril/calibration-policy.md) when calibration metadata disagree or data must be split.
- Read [optional processors](references/siril/external-processors.md) before installing or running GraXpert, StarNet, or RC-Astro.
- Read [visual feedback](references/siril/visual-feedback.md) before judging previews, references, star layers, or final acceptance.

## Use the unified CLI

Set an absolute path:

```bash
ASTRO=/absolute/path/to/astro-processing/scripts/astro.py
```

Discover the environment without installing anything:

```bash
python3 "$ASTRO" doctor --project /absolute/project
```

Inspect input without writing to it:

```bash
python3 "$ASTRO" inspect --input /absolute/input
```

Resolve the route, support level, environment risks, network, and resource estimate:

```bash
python3 "$ASTRO" preflight \
  --input /absolute/input \
  --output /absolute/output \
  --backend auto \
  --profile balanced
```

Summarize the main backend, processors, fallbacks, maturity, data warnings, and time/space implications in one message. Do not create a run until the user confirms the initial route. If the user accepts a displayed scientific warning, record the exact code rather than using a generic force flag:

```bash
python3 "$ASTRO" plan \
  --input /absolute/input \
  --output /absolute/output \
  --profile balanced \
  --accept-warning DARK_TEMPERATURE_MISMATCH \
  --confirm-route
```

The run contains `run-config.yaml` with the resolved configuration, decision sources, detected tools, route, fallbacks, data profile, accepted warnings, and route fingerprint. Never reconstruct these decisions only from conversation history.

## Route simply and deterministically

Choose the main backend in this order:

1. Explicit user requirement.
2. Current-request selection.
3. Project preference.
4. User-global preference.
5. Validated available implementation.
6. Siril as the default validated backend.

For each optional stage use `require`, `prefer`, `auto`, or `disabled`. Never silently replace `require`. For `prefer` and `auto`, select the first validated available candidate and retain only validated allowed fallbacks. `quality` may propose an experimental implementation but requires confirmation; `balanced`, `fast`, and `native-only` must not silently select experimental implementations.

Execution profiles:

- `balanced`: default; prefer validated GraXpert/StarNet or licensed RC-Astro when useful.
- `quality`: allow longer processing and propose experimental capabilities with confirmation.
- `fast`: minimize external stages and retries.
- `native-only`: use only the main backend and establish a diagnostic baseline.

## Respect support levels

- `validated`: current OSC deep-sky and comet Siril workflows on tested platforms and tested optional processors.
- `experimental`: installed capabilities that need a version/platform smoke test before automatic routing, including initial PixInsight PJSR and RC-Astro adapters.
- `adaptive`: mono/LRGB/narrowband or other unvalidated profiles. Generate a reviewable plan from the shared stage contract, do not guess filter/channel semantics, and obtain one initial confirmation.
- `unavailable` or `broken`: never route automatically.

Do not claim a whole software package is validated. Maturity belongs to platform × software × stage × data profile. After a software upgrade, treat affected capabilities as experimental until smoke-tested.

## Execute the Siril compatibility path

Phase one delegates validated pixel processing to the bundled Siril engine. After the unified plan freezes the route, use the same unified CLI for Siril-backed run commands:

```bash
python3 "$ASTRO" run --run /absolute/run
python3 "$ASTRO" postprocess --run /absolute/run --params /absolute/params.json
python3 "$ASTRO" comet --run /absolute/run --group group-id ...
python3 "$ASTRO" select --run /absolute/run --group group-id --attempt attempt-001
python3 "$ASTRO" report --run /absolute/run
python3 "$ASTRO" cleanup --run /absolute/run --profile standard
```

Follow the directly linked Siril references for calibration, moving-object processing, visual retries, exports, and cleanup. PixInsight execution remains experimental until its PJSR stages are forward-tested; environment discovery must not be presented as working execution.

## Install only after consolidated approval

Keep `doctor` read-only. When a required dependency is missing, identify a verified system package manager, project-official package, or official installer. Present the exact source, command, version, size, license implications, and fallback in the preflight summary. Install only after approval. Do not install a package manager itself. Never store license keys or authentication data. Re-run discovery, compatibility checks, and a smoke test after installation.

## Finish only after three gates

Use these states:

- `complete`: data integrity, technical execution, and visual QC all pass.
- `complete_with_degradation`: the user explicitly accepts a recorded compromise.
- `needs_review`: commands completed but scientific or visual QC failed.
- `failed`: data integrity or technical execution failed.
- `blocked`: an external dependency or required user decision prevents progress.

Only `complete` and user-accepted `complete_with_degradation` runs may use `minimal` cleanup. Preserve logs, parameters, previews, provenance, and final exports needed to learn from failures.

## Improve without self-modifying silently

Record each reusable finding with symptom, earliest responsible stage, evidence, scope, confidence, workaround, and proposed change. Store candidate learnings outside the production rules. Promote a safety fix after one reproducible case; promote default/routing changes only after representative cases and regression coverage. Modify and publish the skill only after user approval.
