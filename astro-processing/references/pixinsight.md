# PixInsight adapter policy

## Contents

- Execution contract
- Implemented route
- Validation matrix
- Format boundary
- Known limits

## Execution contract

Use `scripts/astro.py` for doctor, capability probe, preflight, plan, dry-run, execution, reconciliation, and visual review. Keep XISF for all PixInsight-internal checkpoints. The generated `pjsr/pixinsight_pipeline.js` reads an attempt-local frozen JSON parameter file and atomically publishes one result JSON.

The tested macOS launch form is:

```text
<PixInsight.app>/Contents/MacOS/PixInsight --execute=<generated-script.js>
```

This sends PJSR to an already-running PixInsight GUI instance. It is not headless. The launcher can return before processing finishes. Authenticate all three fields before technical success: `ok: true`, the exact attempt `execution_id`, and `successMarker: ASTRO_PROCESSING_PIXINSIGHT_OK`. Incomplete JSON is retried while polling. A failed or timed-out attempt is immutable; a late JSON stays quarantined there and cannot satisfy another attempt.

Each execution and retry creates `<run>/attempts/<unique-id>/` with its own parameters, generated script, log, checkpoints, exports, previews, result, and state. Technical success sets both attempt and run to `needs_review`, never `complete`. Inspect the ABE corrected/model previews, RC starless/stars previews when applicable, and final reconstruction preview, then use `review-pixinsight --verdict accept` or `reject`. The review must record notes or at least one issue; an evidence-free verdict is refused. Linear diagnostic previews use an unlinked display stretch on a disposable clone; they never alter a checkpoint. Accept is the only transition to `complete`, and a completed run is terminal. After rejection, pass an allowlisted tuning JSON to `run --params`; this creates a new attempt and cannot change the frozen input, executable, processors, models, route, runtime tuple, or route fingerprint. Use `reconcile-pixinsight` only to authenticate a complete result from an attempt whose launcher stopped polling.

The plan freezes the input path, byte size, modification time, SHA-256 digest, resolved absolute PixInsight executable, exact processor route, and route fingerprint. Execution and review recompute the input identity and unified contract, and refuse a changed input at the same path, executable override, modified route, modified attempt parameters/script, or conflicting explicit processor requirement. An external processor selected explicitly is a blocker until a supported 32-bit FITS boundary adapter exists; it is never silently replaced by the consolidated PJSR path.

## Implemented route

The automatic route accepts one declared `integrated-linear` `osc-color` Float32 RGB XISF. It does not run calibration, registration, integration, WBPP, DBE, ImageSolver, or SPCC.

The native route is ABE degree 1 → BackgroundNeutralization → classic structure-detected ColorCalibration → MLT linear denoise → linked HistogramTransformation → restrained ColorSaturation → exports.

The optional licensed route is ABE → classic color → BXT AI4 → SXT AI11 lite nonoise with retained starless/stars → NXT AI3 on the linear starless layer only → independent layer stretches → screen reconstruction → exports. First run `probe-pixinsight`: filesystem discovery must find each module, version marker, and bundled-model token, and the PJSR probe must construct each process with a selected model. This probe does not test a license. Separately require the user to confirm active local licenses. Routing is fail-closed unless discovery, runtime probe, and confirmation all pass. Do not install modules, models, or astronomy dependencies.

Treat classic ColorCalibration as a recorded non-photometric degradation. Never describe it as SPCC.

## Validation matrix

Maturity is scoped to every row; it does not validate PixInsight generally.

| OS / host arch | PI executable arch | PixInsight | Stage | Data type | Evidence | Maturity |
|---|---|---|---|---|---|---|
| macOS 15.6.1 / arm64 | x86_64 under macOS translation | 1.9.3 build 1646 | `--execute` GUI IPC, result polling | synthetic RGB Float32 | generated-script smoke | validated for this tuple only |
| same | same | same | ABE degree 1; BN + classic ColorCalibration; MLT; linked stretch; saturation | one post-integration OSC RGB Float32 frame | real workflow logs, 6219×4139×3 | validated for this tuple only |
| same | same | same | BXT 2.1.4 AI4; SXT 2.4.11 AI11; NXT 2.3.3 AI3; layer stretch/reconstruction | same frame, active licenses user-confirmed | real workflow logs, WCS retained | validated for this tuple only |
| same | same | same | XISF/TIFF/PNG/JPEG save and reopen | same final RGB image | dimensions/channels/depth verified | validated for this tuple only |
| same | same | same | 32-bit FITS save/reopen gate | synthetic linear RGB Float32 | dimensions/channels/depth/declared linear state/WCS state/orientation checks | validated only inside PI; external round trip experimental |
| same | same | same | isolated attempt, authenticated result, reject/tune/retry/accept state machine | 1554×1034 full-field downsample of one real post-integration OSC RGB Float32 frame | four real RC attempts, distinct execution IDs/directories, local visual review, sanitized summary; current hardening pending rerun | experimental until current gate revision is rerun on PI |
| any other tuple | any | any | all stages | any | no direct evidence | experimental |

After a PixInsight or RC-Astro upgrade, treat the affected rows as experimental until rerunning smoke and a representative real case.

## Format boundary

Use XISF between PI stages. Use FITS only at an explicit cross-software boundary. The adapter writes Float32 FITS and reopens it in PI, checking width, height, channels, `bitsPerSample=32`, real sample format, declared linear state, WCS presence, and a fixed corner/center pixel signature for orientation. A boundary fails closed when a required check differs.

TIFF/PNG/JPEG are delivery exports, not PI working checkpoints. TIFF/PNG/JPEG may not preserve WCS; record that loss instead of failing a delivery export. An external tool round trip is still experimental until it is tested independently.

## Known limits

- PixInsight must already be open and able to accept `--execute` IPC.
- `doctor` discovers the application, module files, module version markers, and bundled-model tokens but does not prove that the GUI is running or that licenses are active. `probe-pixinsight` proves process construction/model selection only; license confirmation remains separate.
- Linearity is a declared semantic property and is also enforced by stage order; PI does not expose a universal reliable “linear” file flag.
- WBPP, calibration, registration, integration, DBE, SPCC, automated ImageSolver, mono/LRGB/narrowband, comet data, external FITS round trips, and general export behavior remain experimental.
- PJSR process properties and RC-Astro model names are version-specific.
- Commit no input frames, observation-derived previews, full-size outputs, models, binaries, licenses, absolute home paths, or unsanitized logs. Lightweight sanitized summaries may live under `references/evidence/`; attach temporary review images outside Git when collaboration requires them.
