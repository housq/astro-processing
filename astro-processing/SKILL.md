---
name: astro-processing
description: Inspect, calibrate, clean, register, integrate, visually refine, and export astrophotography through one environment-aware workflow that routes between Siril and PixInsight main backends and optional RC-Astro, GraXpert, StarNet, and SETI Astro processors. Use for color-camera or OSC deep-sky and comet data; for selecting astronomy software from user requirements, preferences, installed versions, licenses, models, platform maturity, and resource limits; for resumable FITS/XISF processing with visual feedback; or for adaptive planning of unvalidated mono/LRGB/narrowband data.
---

# Astro Processing

Use one user-visible workflow while keeping each software implementation isolated behind an adapter. Treat Siril and PixInsight as mutually exclusive main backends for a run unless the user explicitly approves a mixed-backend plan. Treat GraXpert, StarNet, licensed RC-Astro BXT/NXT/SXT, and experimental SETI Astro Cosmic Clarity as optional stage processors.

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
- Read [SETI Astro](references/setiastro.md) before routing or comparing SETI Astro stages.
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

## Execute a frozen backend

After the unified plan freezes the route, use the same unified CLI. Siril supports the original raw-data commands:

```bash
python3 "$ASTRO" run --run /absolute/run
python3 "$ASTRO" postprocess --run /absolute/run --params /absolute/params.json
python3 "$ASTRO" comet --run /absolute/run --group group-id ...
python3 "$ASTRO" select --run /absolute/run --group group-id --attempt attempt-001
python3 "$ASTRO" report --run /absolute/run
python3 "$ASTRO" cleanup --run /absolute/run --profile standard
```

Run a frozen optional processor through a checkpointed attempt instead of invoking its binary by hand:

```bash
python3 "$ASTRO" stage \
  --run /absolute/run \
  --group group-id \
  --stage background_extraction \
  --reference /absolute/reference.png \
  --reference-notes "Protect the faint outer halo"
```

Stop Siril at a software boundary instead of running duplicate later stages. For example, after accepting external BGE, run only color calibration from that checkpoint, then pass the resulting `color.fit` explicitly to StarNet:

```bash
python3 "$ASTRO" postprocess \
  --run /absolute/run \
  --start-stage color \
  --end-stage color \
  --from-checkpoint /absolute/accepted-background.fit

python3 "$ASTRO" stage \
  --run /absolute/run \
  --group group-id \
  --stage star_separation \
  --input /absolute/attempt/color.fit
```

Open every generated `preview-*.png` and compare it with the input, model/layer previews, and any user references at overview and 100%. Do not promote a command merely because it exited successfully. Record the visual result:

```bash
python3 "$ASTRO" review-stage \
  --run /absolute/run \
  --group group-id \
  --attempt background-extraction-001 \
  --verdict accept \
  --notes "Gradient removed; no target structure in the background model"
```

For a bad result, use `--verdict reject --issue "..."`, adjust a copied JSON parameter file, and run `stage` again. Every retry gets a new directory; never overwrite the rejected checkpoint. Use `--processor` only for the frozen selection or recorded fallback. SETI Astro denoise and starless runs additionally require `--ab-candidate`; an accepted A/B result is not promoted until a route change is confirmed. Super resolution is not exposed by the adapter.

Follow the directly linked references for calibration, moving-object processing, routed stages, visual retries, exports, and cleanup. PixInsight discovery alone must not be presented as working execution; apply only the exact stage/data/platform maturity recorded in its validation matrix.

For a post-integration OSC color XISF, declare the semantic state and require PixInsight explicitly:

```bash
python3 "$ASTRO" preflight \
  --input /absolute/integrated-linear.xisf \
  --output /absolute/output \
  --backend pixinsight \
  --input-state integrated-linear \
  --data-type osc-color \
  --profile balanced
python3 "$ASTRO" plan ... --confirm-route
python3 "$ASTRO" run --run /absolute/output/run-id --dry-run
python3 "$ASTRO" run --run /absolute/output/run-id
python3 "$ASTRO" review-pixinsight --run /absolute/output/run-id --attempt attempt-id --verdict accept --notes "Visual QC passed"
```

Read [pixinsight.md](references/pixinsight.md) before removing `--dry-run`. PixInsight uses XISF checkpoints and a generated PJSR script. Every execution and retry gets an immutable attempt directory. The implemented launcher submits `--execute=<script>` to a running GUI instance and authenticates the frozen execution contract, runtime tuple, complete stage/export/preview manifest, `ok`, `successMarker`, and `execution_id`; it is not a headless CLI. Technical success stops at `needs_review`. Only `review-pixinsight --verdict accept` with recorded notes or issues may mark the run `complete`; a completed run is terminal. Reject, tune an allowlisted parameter JSON, and run again to create a new attempt.

Before selecting RC-Astro, run `probe-pixinsight --output /absolute/probes` and pass its result JSON to preflight/plan with `--pixinsight-probe`. Module/version/bundled-model discovery and the PJSR construction probe are independent of license confirmation. Add `--rc-astro --confirm-rc-astro-license` only after the user confirms active local BXT/SXT/NXT licenses. Confirmation alone never makes a missing or unprobed module available. Never store license data.

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
