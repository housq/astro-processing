# Optional astronomy processors

Read this reference before installing or running StarNet, GraXpert, or the RC-Astro CLI. Keep every input and output checkpoint; never replace the only calibrated linear FITS.

## Discovery and consent

Run:

```bash
python3 "$PIPELINE" doctor
```

Search `PATH`, environment overrides, Siril's global preferences, standard application locations, and known portable locations. Discovery is read-only. Treat a configured path as unavailable until the executable and required model/weights exist.

Obtain confirmation before downloading or installing a missing tool or model. Prefer official sources and an architecture-matched build. Record the product version, model version, command, parameters, execution provider, exit status, and output paths. Paid RC-Astro products require an existing license or a user-authorized trial; never request, infer, print, or store a license key.

## Preferred order

For ordinary OSC data use this order, skipping unavailable stages:

1. Calibrate, debayer, register, and stack in Siril.
2. Apply BXT only to the untouched linear image when licensed and appropriate. Never apply deconvolution after denoising.
3. Apply linear BGE using GraXpert AI or Siril `subsky`; inspect the background model for removed target signal.
4. Plate solve, then run PCC/SPCC. Do not proceed if requested online calibration failed.
5. Separate stars on the calibrated linear image with SXT or StarNet.
6. Denoise the starless linear layer with NXT, GraXpert, or Siril; keep the star mask free of denoising.
7. Stretch the starless target layer and star layer independently.
8. Recompose in Siril PixelMath and inspect bright stars and the target at 100%.

For comets, protect the coma and tail during BGE. Inspect the star mask for a nucleus residual; subtract or softly mask that residual before recomposition so the nucleus is not added twice.

## GraXpert CLI

Official macOS executable inside the application bundle:

```text
GraXpert.app/Contents/MacOS/GraXpert
```

Linear BGE example:

```bash
"$GRAXPERT" input.fit -cli -cmd background-extraction \
  -output output-base -gpu true -ai_version 1.0.1 \
  -correction Subtraction -smoothing 0.1 -bg
```

Use subtraction for ordinary additive sky gradients. Consider division only for a verified multiplicative illumination residual. Always inspect the saved background model; reject it if a comet, galaxy halo, nebula, or tail is recognizable.

Linear denoise example:

```bash
"$GRAXPERT" starless.fit -cli -cmd denoising \
  -output starless-denoised -gpu true -ai_version 3.0.2 \
  -strength 0.35 -batch_size 4
```

Start around 0.25–0.4. Compare at 100%; lower strength when filaments look waxy, blocky, or invented.

## StarNet through Siril

Siril preferences need both the StarNet CLI path and, for the Torch build, its weights. Use Siril's integration for a linear FITS:

```text
load calibrated-linear.fit
starnet -stretch
```

`-stretch` applies a temporary MTF, invokes StarNet, then reverses the stretch. Siril saves 32-bit FITS outputs named `starless_<input>.fit` and `starmask_<input>.fit`. The loaded image becomes starless.

Check that:

- the starless layer retains all nonstellar structure and has no large star holes;
- the star mask contains stars, not comet coma, galaxy cores, or compact nebular knots;
- adding the untouched two linear layers approximately reconstructs the calibrated input.

Stretch the starless layer more strongly and the star mask more gently. Use scripted Siril `pm` for reproducible recomposition. Avoid aggressive star-mask sharpening or saturation that creates black rings, clipped cores, or chromatic halos.

## RC-Astro CLI

The stand-alone executable is `rc-astro`; products are subcommands `bxt`, `nxt`, and `sxt`. Discover supported products and machine-readable parameters from the installed CLI instead of hard-coding a changing beta/release interface. Require activated licenses.

- BXT: linear data only, before denoise; start conservatively and reject ringing or false micro-detail.
- NXT: usually linear after deconvolution/BGE/color calibration; compare faint structure at 100%.
- SXT: linear star separation; retain both starless and star layers and inspect target leakage.

If `rc-astro` is absent, continue with the StarNet/GraXpert/Siril route. Do not install RC-Astro merely because the OS supports it; the user must confirm and have a license or authorize a trial.

## Failure policy

An external-tool failure must not overwrite a valid checkpoint or silently switch algorithms. Record the failure and either retry with corrected settings or continue from the prior checkpoint with an explicitly reported fallback. A command's zero exit status does not replace visual inspection of its image and model outputs.
