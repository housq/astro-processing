# Siril CLI 1.4 reference

Use this reference when reviewing or changing generated `.ssf` files. Target the stable Siril 1.4 command set. Verify newer or older versions against the official command index before using syntax not listed here.

Official sources:

- Stable commands: https://siril.readthedocs.io/en/stable/Commands.html
- Script files: https://siril.readthedocs.io/en/stable/scripts/Script-files.html
- Calibration: https://siril.readthedocs.io/en/stable/preprocessing/calibration.html
- Registration: https://siril.readthedocs.io/en/stable/preprocessing/registration.html
- Official OSC preprocessing example: https://gitlab.com/free-astro/siril/-/blob/945c7e65e1afe7df84bd0d3295e5300a22eb5906/scripts/OSC_Preprocessing.ssf

## Invocation and quoting

Run a script with:

```bash
siril-cli -s /absolute/script.ssf
```

Require the supported version at the top:

```text
requires 1.4.0
```

Spaces delimit Siril arguments. For an option whose value contains spaces, quote the entire option:

```text
command "-out=/path/with spaces/result"
```

Do not write `-out="/path/with spaces/result"`.

## Conversion and masters

`convert basename -out=directory` converts supported files in the current working directory to a Siril FITS sequence. FITS inputs may become symbolic links; RAW inputs are decoded.

Use no normalization for bias and dark masters:

```text
stack bias rej w 3 3 -nonorm -32b -out=master-bias
stack dark rej w 3 3 -nonorm -32b -out=master-dark
```

Calibrate flats with a bias or matching dark-flat, then use multiplicative normalization:

```text
calibrate flat -bias=master-bias.fit -cfa
stack pp_flat rej w 3 3 -norm=mul -32b -out=master-flat
```

Do not debayer masters. Keep CFA data mosaiced until light calibration.

## Light calibration

Typical OSC calibration using a raw master dark that still contains its bias signal:

```text
calibrate light -dark=master-dark.fit -flat=master-flat.fit -cc=dark -cfa -equalize_cfa -debayer
```

Without a compatible dark, bias and flat calibration is valid:

```text
calibrate light -bias=master-bias.fit -flat=master-flat.fit -cfa -equalize_cfa -debayer
```

`-opt` requires both bias and dark masters. Use it only as an explicitly labeled comparison for a scientifically defensible scaling case; CMOS amp glow and temperature mismatch may not scale cleanly.

## Registration and rejection

Compute a robust reference before applying transforms:

```text
register pp_light -2pass -selected
seqapplyreg pp_light -framing=min -interp=lanczos4
```

Siril exposes filters for FWHM, weighted FWHM, roundness, background, star count, quality, and included state. Values ending in `k` are sigma-based cutoffs. Multiple filters combine; check the resulting exclusion ratio rather than assuming a cutoff is safe.

Example stack:

```text
stack r_pp_light rej w 3 3 -norm=addscale -output_norm -weight=wfwhm -32b \
  -filter-wfwhm=3k -filter-round=3k -filter-bkg=3k -filter-nbstars=3k \
  -out=stacked-linear
```

If more than 30% are rejected, relax to 4k or 5k, remove redundant filters one at a time, and compare the stack. A registration failure is not the same as quality-filter rejection.

## Moving objects

Siril 1.4's comet/asteroid registration is a GUI-only registration method. Its implementation computes object velocity from two `DATE-OBS` timestamps and positions, computes each frame's time-relative displacement, and left-composes the inverse displacement with any existing star-registration homography. The helper mirrors this sequence-metadata operation and uses CLI `seqapplyreg` for the actual resampling.

For dual registration, use exactly the same calibrated frames and reference frame for both layers. Apply Winsorized rejection with additive scaling to the star-aligned set and to the comet-aligned set. Inspect each layer before combining. Because independent output normalization can leave the two stacks at different linear levels, run `linear_match` on the comet layer against the star layer before PixelMath. Without external star removal, create a soft local mask around the nucleus and tail and feather the background-matched comet layer into the star layer. Inspect for a star-stack rejection hole at the nucleus, moving-star residuals, or a visible mask seam. StarNet is external and must not be installed without user confirmation.

Official workflow: https://siril.org/tutorials/comet/

## Metadata and previews

Dump machine-readable metadata and statistics:

```text
jsonmetadata image.fit -stats_from_loaded -out=image.json
```

Create a display preview without overwriting the linear checkpoint:

```text
load image.fit
autostretch -linked -2.8 0.18
resample -maxdim=2048 -interp=area
savepng image-preview
```

Reload the saved FITS checkpoint after making a preview if further linear processing follows.

## Linear post-processing

Polynomial or RBF background extraction:

```text
subsky 1 -samples=20 -tolerance=1.0
subsky -rbf -samples=20 -tolerance=1.0 -smooth=0.5
```

Do not use background extraction to conceal flat-field errors, reflections, or stacking seams.

Plate solve from valid FITS metadata, then perform PCC:

```text
platesolve -downscale
pcc -catalog=apass
```

Use `-force` only when replacing a stale or incorrect solution. If coordinates, focal length, or pixel size are wrong, correct them rather than repeatedly blind-solving.

SPCC requires exact installed names:

```text
spcc_list oscsensor
spcc "-oscsensor=Exact Sensor Name" "-oscfilter=Exact Filter Name"
```

Never invent sensor or filter names.

Conservative stacked-image denoise:

```text
denoise -mod=0.45
```

`-da3d` and `-sos` cost more and can create artifacts. Compare at 100% scale and in smooth background areas.

## Stretch and finish

Linked stretches preserve calibrated color balance better than unlinked stretches. A conservative first display stretch is:

```text
autostretch -linked -2.8 0.15
```

AutoGHS offers finer highlight control when the automatic stretch is too harsh:

```text
autoghs -linked -2.8 3.0 -clipmode=rgbblend
```

Other supported options include `autostretch`, `ght`, and `modasinh`. Protect star cores and galaxy nuclei; do not choose parameters solely to brighten faint structure.

Optional finishing commands:

```text
rmgreen 0 0.35
satu 0.12 1.0 6
unsharp 1.0 0.15
```

Do not run `rmgreen` automatically after successful photometric calibration unless a verified residual cast remains. Use sharpening sparingly and reject dark halos or ringing.

## Exports

```text
save stacked-linear
savetif final -deflate
savepng final
savejpg final 95
```

`save` writes FITS. `savetif` writes 16-bit TIFF, `savepng` writes 16-bit PNG for a 16/32-bit loaded image, and JPEG is 8-bit lossy output. Preserve the linear 32-bit FITS independently of nonlinear exports.
