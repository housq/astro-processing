# PixInsight adapter policy

## Contents

- Execution contract
- Implemented route
- Validation matrix
- Format boundary
- Known limits

## Execution contract

Use `scripts/astro.py` for doctor, preflight, plan, dry-run, and execution. Keep XISF for all PixInsight-internal checkpoints. The generated `pjsr/pixinsight_pipeline.js` reads a frozen JSON parameter file and writes a unique result JSON.

The tested macOS launch form is:

```text
<PixInsight.app>/Contents/MacOS/PixInsight --execute=<generated-script.js>
```

This sends PJSR to an already-running PixInsight GUI instance. It is not headless. The launcher can return before processing finishes, so accept success only after the expected result JSON contains `ok: true` and `ASTRO_PROCESSING_PIXINSIGHT_OK`.

## Implemented route

The automatic route accepts one declared `integrated-linear` `osc-color` Float32 RGB XISF. It does not run calibration, registration, integration, WBPP, DBE, ImageSolver, or SPCC.

The native route is ABE degree 1 → BackgroundNeutralization → classic structure-detected ColorCalibration → MLT linear denoise → linked HistogramTransformation → restrained ColorSaturation → exports.

The optional licensed route is ABE → classic color → BXT AI4 → SXT AI11 lite nonoise with retained starless/stars → NXT AI3 on the linear starless layer only → independent layer stretches → screen reconstruction at 0.70 star strength → exports. Require the user to confirm active local licenses. Do not install modules, models, or astronomy dependencies.

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
| any other tuple | any | any | all stages | any | no direct evidence | experimental |

After a PixInsight or RC-Astro upgrade, treat the affected rows as experimental until rerunning smoke and a representative real case.

## Format boundary

Use XISF between PI stages. Use FITS only at an explicit cross-software boundary. The adapter writes Float32 FITS and reopens it in PI, checking width, height, channels, `bitsPerSample=32`, real sample format, declared linear state, WCS presence, and a fixed corner/center pixel signature for orientation. A boundary fails closed when a required check differs.

TIFF/PNG/JPEG are delivery exports, not PI working checkpoints. TIFF/PNG/JPEG may not preserve WCS; record that loss instead of failing a delivery export. An external tool round trip is still experimental until it is tested independently.

## Known limits

- PixInsight must already be open and able to accept `--execute` IPC.
- `doctor` discovers the application and version but does not prove that the GUI is running or that licensed modules work.
- Linearity is a declared semantic property and is also enforced by stage order; PI does not expose a universal reliable “linear” file flag.
- WBPP, calibration, registration, integration, DBE, SPCC, automated ImageSolver, mono/LRGB/narrowband, comet data, external FITS round trips, and general export behavior remain experimental.
- PJSR process properties and RC-Astro model names are version-specific.
- Do not commit images, models, binaries, licenses, absolute home paths, or unsanitized logs. Keep lightweight evidence under `references/evidence/`.
