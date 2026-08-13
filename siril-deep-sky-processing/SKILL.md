---
name: siril-deep-sky-processing
description: Calibrate, clean, register, stack, visually diagnose, iteratively post-process, and export color-camera deep-sky and comet astrophotography with Siril CLI. Use for DSLR or mirrorless RAW files and one-shot-color astronomy-camera FITS datasets containing lights plus any combination of flats, darks, biases, or dark-flats; for automatic frame classification and compatibility grouping; for quality-based bad-frame rejection; for time-based comet or asteroid registration with dual star/object stacks; or for producing linear FITS masters and finished TIFF, PNG, and JPEG images with visual feedback and resumable retries.
---

# Siril Deep-Sky Processing

Produce a reproducible OSC deep-sky image without modifying source data. Use Siril as the required processing engine and optionally use user-approved StarNet, GraXpert, or licensed RC-Astro CLI tools for specialized BGE, star separation, deconvolution, and denoising. Use the bundled Python helper for discovery, grouping, staging, dependency checks, script generation, checkpoints, reports, and guarded cleanup.

## Load references selectively

- Read [siril-cli.md](references/siril-cli.md) before changing generated Siril commands, diagnosing a Siril error, or using a command not already emitted by the helper.
- Read [calibration-policy.md](references/calibration-policy.md) when metadata are missing, calibration frames disagree, multiple sessions are present, or deciding whether to branch.
- Read [visual-feedback.md](references/visual-feedback.md) before evaluating a preview, adjusting post-processing, or interpreting a user reference image.
- Read [external-processors.md](references/external-processors.md) before installing or running StarNet, GraXpert, BXT, NXT, or SXT.

## Establish the contract

1. Resolve the input directory and an output location outside the source tree when possible.
2. Ask for optional text criteria, target values, or reference images before processing. Accept new criteria and references after any attempt as well.
3. Default to a natural result: neutral background, protected highlights, restrained saturation and denoising, and stars without dark halos.
4. Treat `raw` output as a 32-bit linear FITS stack, not a recreated camera RAW file.
5. Never rename, edit, move, or delete source files. The helper creates standardized staging links, so irregular source names do not need renaming.

## Use the helper

Set this once for readable commands:

```bash
PIPELINE=/absolute/path/to/this-skill/scripts/astro_pipeline.py
```

Use an absolute path in actual calls. Do not assume the skill's install location.

### 1. Inspect without writing

```bash
python3 "$PIPELINE" inspect --input /absolute/input
```

Review counts, groups, quarantine, and every high-severity warning. Classification priority is FITS metadata, then directory tokens, then filename tokens. Unknown files remain quarantined; never guess their type.

Inspect representative FITS headers or camera metadata when important fields are absent. Confirm that the data are Bayer/OSC, not monochrome multi-filter data.

### 2. Check Siril and disk safety

Locate `siril-cli` from `PATH`, `SIRIL_CLI`, or the macOS application bundle. Require Siril 1.4 or newer. If absent or incompatible, explain the exact installation or upgrade and obtain confirmation before changing the system.

Discover optional processors without installing anything:

```bash
python3 "$PIPELINE" doctor
```

The doctor also reads existing Siril preferences, so a configured StarNet installation is not lost when the run uses an isolated Siril runtime. Obtain confirmation before downloading a missing program or AI model. Treat RC-Astro BXT/NXT/SXT as available only when `rc-astro` exists and the needed license is activated.

Create the run only after inspection:

```bash
python3 "$PIPELINE" plan \
  --input /absolute/input \
  --output /absolute/output \
  --run-id descriptive-run-id

python3 "$PIPELINE" preflight --run /absolute/output/descriptive-run-id
```

Do not start if estimated working space exceeds free space. Prefer `standard` cleanup after success; do not lower the safety estimate merely to make the check pass.

### 3. Resolve calibration warnings

Use compatible calibration frames automatically. Treat geometry, camera, binning, gain/ISO, offset, Bayer pattern, filter, and relevant exposure as compatibility constraints.

Do not silently use temperature-mismatched darks. When the closest dark set exceeds the default 5 °C tolerance:

- prefer a matching dark set if the user can provide one;
- otherwise process the scientifically safer no-dark branch first;
- optionally compare an optimized mismatched-dark branch by passing `--allow-risky-dark` and label it clearly;
- select by residual hot pixels, amp glow, background noise, clipping, and detail—not by intuition alone.

### 4. Generate and run preprocessing

Render scripts first when risk is high:

```bash
python3 "$PIPELINE" run --run /absolute/run --dry-run
```

Inspect `groups/<group>/scripts/preprocess.ssf`, then execute:

```bash
python3 "$PIPELINE" run --run /absolute/run
```

Use `resume` after interruption. Use `--group <id>` for one group. Use `--force` only after preserving a good linear checkpoint.

The generated workflow creates masters, calibrates CFA data, debayers, registers in two passes, applies registration with common-area framing, and stacks to 32-bit FITS with Winsorized rejection and registration-quality filters.

### 4a. Branch to a comet or asteroid workflow

First perform ordinary star registration and inspect several widely separated registered previews. A moving diffuse nucleus or point source that follows a time-linear path while surrounding stars remain fixed is evidence for this branch. Do not infer a comet merely from a catalog target name.

Measure the object center in at least the first and last usable registered frames; prefer three or more checkpoints and reject a fit contaminated by nearby stars. Fit preview X/Y position against `DATE-OBS` in pixels per hour. Preview coordinates use positive X to the right and positive Y downward. Record the points, residuals, selected frames, and reference frame. Accept user-supplied ephemerides, criteria, or reference images before or after this measurement.

Siril 1.4 exposes moving-object registration only through its GUI. The helper therefore reproduces Siril 1.4's own time-shift model in a derived `.seq` file, then keeps all pixel resampling, rejection stacking, combination, preview generation, and post-processing inside Siril CLI. It never edits the calibrated pixels itself.

Run a dark-calibrated dual registration with a retained set of masters:

```bash
python3 "$PIPELINE" comet \
  --run /absolute/run \
  --group group-id \
  --velocity-x -9.1 \
  --velocity-y 178.8 \
  --reference-frame 16 \
  --object-x 2993 \
  --object-y 977 \
  --include 1-21,23-28,30-32 \
  --allow-risky-dark
```

Omit `--allow-risky-dark` when the dark master is compatible. Inspect all three outputs in `groups/<group>/comet`: `star_aligned_linear.fit`, `comet_aligned_linear.fit`, and the combined `comet-linear.fit`. Confirm sharp stationary stars in the first, a sharp nucleus/tail with rejected stellar trails in the second, and no doubled stars, holes, seams, or clipped comet core in the combined preview. If trails remain, adjust the included frames or rejection settings and retry; if the nucleus is soft or doubled, correct the velocity or use a shorter time segment. Keep separate session tracks when the motion is not well described by one velocity.

Before combining, match the comet layer's linear background and scale to the star layer. Never apply a raw pixelwise maximum when separately normalized stacks have different backgrounds: the brighter layer will suppress the other globally, and a star stack may contain a rejection hole at the moving nucleus. The helper performs Siril `linear_match`, builds a recorded feather mask around `--object-x/--object-y`, and inserts the comet-aligned core/tail locally. Adjust `--mask-width`, `--mask-height`, and `--mask-blur` to cover the visible tail without importing unnecessary moving-star residuals. Then it promotes `comet-linear.fit` to the run's linear checkpoint after a successful dual stack. Continue with the normal visual-feedback and post-processing stages. The optional StarNet route may improve difficult star/comet separation, but it is an external dependency: detect availability first and obtain user confirmation before installation.

The comet command trims 40 pixels per edge by default after recombination to remove interpolation borders; set `--border-crop 0` only when verified edge content must be retained.

After stacking:

1. Read the Siril log and determine how many lights were excluded or failed registration.
2. Keep a reason for every exclusion: registration failure, wFWHM/FWHM, roundness, background, star count, or manual visual defect.
3. If more than 30% of lights are excluded, do not accept the stack automatically. Retry with a larger `--quality-k` such as 4 or 5, remove one questionable filter at a time in the generated script if necessary, or ask the user when the evidence is ambiguous.
4. Compare the original and relaxed stacks. Do not equate fewer frames with a better result.

### 5. Inspect the linear stack

Open `stacked-linear-preview.png` with the available image-viewing tool. Also read `stacked-linear.json` and the preprocessing log. Check registration edges, doubled stars, trails, hot-pixel streaks, flat-field residuals, clipping, gradients, Bayer artifacts, and channel imbalance.

If calibration or registration is wrong, fix that upstream stage and restack. Do not hide structural defects with denoising, background extraction, or sharpening.

### 6. Create a post-processing attempt

Edit a copy of `params.json` for the attempt. Keep the user criteria and reference interpretation in `reference_notes`. Start with the natural defaults.

```bash
python3 "$PIPELINE" postprocess --run /absolute/run --params /absolute/params.json
```

The attempt saves linear previews and FITS/JSON checkpoints after background extraction, color calibration, and denoising; it also saves stretched and finished previews plus TIFF, PNG, and JPEG candidates.

Online behavior:

- Prefer plate solving plus PCC when metadata and network access permit.
- Use SPCC only with exact sensor/filter names returned by Siril `spcc_list`; never invent a profile.
- If solving or catalog access fails, preserve the prior checkpoint, record the degradation, and retry with corrected coordinates/settings or `color.mode: none`. Do not claim photometric calibration succeeded.
- When photometric calibration was requested, require explicit solve and PCC/SPCC success in the log; a zero process exit alone is insufficient.
- Never run automatic green removal after successful PCC/SPCC. For a comet, blue-green or cyan coma can be real signal. Allow green removal only after a documented visual comparison proves a residual cast outside the target.

### 6a. Run the advanced optional path

Use [external-processors.md](references/external-processors.md) when the user requests BGE, deconvolution, star removal/reconstruction, or AI denoise.

For a high-quality color result, prefer:

1. Optional licensed BXT on untouched linear data.
2. GraXpert AI BGE or Siril `subsky`; inspect the saved background model.
3. Plate solve and PCC/SPCC on the background-corrected linear image.
4. SXT or StarNet linear star separation.
5. NXT, GraXpert, or Siril denoise on the starless layer only.
6. Stronger highlight-protected stretch for the target layer and a gentler stretch for the star mask.
7. Scripted Siril PixelMath star reconstruction, followed by 100% inspection for target leakage, doubled nuclei, black rings, or star halos.

For comet data, inspect the star mask for a residual coma or nucleus and remove that residual with a soft target mask before recomposition. Preserve the expected calibrated cyan/blue-green coma rather than neutralizing it.

### 7. Run the visual feedback loop

Apply [visual-feedback.md](references/visual-feedback.md) to every checkpoint preview and any user reference.

For each meaningful attempt:

1. Record the visible problem and the supporting metric or log evidence.
2. Identify the earliest responsible stage.
3. Preserve the current best checkpoint.
4. Change one causal parameter family at a time.
5. Rerun from that stage by using `--start-stage` and `--from-checkpoint` when an earlier checkpoint remains valid.
6. Compare the new candidate against the best candidate, not only against the immediately previous attempt.
7. Ask the user when two results represent an aesthetic choice rather than an objective defect.

Allow at most 15 attempts per stage by default. Stop a stage after three consecutive attempts without visible or metric improvement. Do not impose a fixed global attempt count. Report progress at least every 30 minutes during a long run.

Never optimize solely for numerical sharpness, saturation, or background darkness. Reject improvements that introduce clipped cores, crushed shadows, plastic texture, ringing, halos, chromatic stars, or lost faint structure.

### 8. Select and export

Select only a completed attempt that has been visually inspected:

```bash
python3 "$PIPELINE" select \
  --run /absolute/run \
  --group group-id \
  --attempt attempt-001
```

Verify the final directory contains:

- `stacked-linear.fit`: 32-bit linear master (`raw` deliverable)
- `final-processed.fit`: selected processed checkpoint
- `final.tif`: 16-bit lossless editor-friendly image
- `final.png`: lossless display image
- `final.jpg`: high-quality sharing image

Open the exported PNG and JPEG once more. Confirm orientation, dimensions, color, absence of clipping/banding, and consistency with the selected attempt.

### 9. Report and clean safely

```bash
python3 "$PIPELINE" report --run /absolute/run
python3 "$PIPELINE" cleanup --run /absolute/run --profile standard
```

The first cleanup call is a dry run. Inspect the exact list and reclaimable size, then pass the printed run ID:

```bash
python3 "$PIPELINE" cleanup \
  --run /absolute/run \
  --profile standard \
  --confirm exact-run-id
```

Use these profiles:

- `keep-all`: delete nothing.
- `standard`: remove staging links and reproducible process sequences after a final attempt is selected; retain masters, the linear stack, attempts, logs, parameters, report, and final outputs.
- `minimal`: additionally remove masters and attempt workspaces after final outputs have been materialized.
- `aborted`: for a run explicitly marked failed or interrupted and with no selected final, remove only staging links and reproducible process sequences. This still requires the exact run-ID confirmation and preserves logs, scripts, manifests, and any masters or checkpoints.

Never bypass the run marker, exact run-ID confirmation, or child-path validation. Do not use broad globs or recursive deletion against an unresolved path.

## Completion criteria

Finish only when source integrity is unchanged, every used calibration set is defensible, rejection remains explainable, a linear FITS stack exists, the selected result passes visual inspection, TIFF/PNG/JPEG exports open correctly, parameters and logs are retained, and cleanup—if requested—completed only inside the marked run.
