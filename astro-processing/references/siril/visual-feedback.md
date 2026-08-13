# Visual feedback and retry policy

Evaluate previews at both fit-to-screen and 100% scale when possible. A downsampled preview reveals global gradients and composition; full resolution reveals star profiles, ringing, chromatic errors, and denoise texture. Use JSON statistics and logs as supporting evidence, not as substitutes for viewing the image.

## Reference criteria

Before processing, translate user text or a reference image into observable criteria:

- background brightness and neutrality
- faint-structure visibility
- core and star highlight protection
- color palette and saturation
- star size and prominence
- noise texture versus smoothing
- crop, orientation, and framing

Do not copy defects from a reference. State when a requested appearance would require clipped highlights, crushed shadows, false color, or detail loss. After processing, accept new references and revise only the affected stage when possible.

## Diagnose the earliest cause

| Symptom | Likely earliest stage | Preferred response |
|---|---|---|
| doubled or streaked stars | registration/light cleaning | inspect failed transforms, reference frame, and rejected lights; restack |
| hot-pixel trails or amp-glow residual | calibration | compare matching dark, optimized risky dark, and no-dark branches |
| strong vignetting or dust donuts | flat calibration | verify flat compatibility, saturation, orientation, and master formula |
| colored checkerboard or zipper artifacts | CFA/debayer | verify Bayer pattern and do not post-process around it |
| seams or black borders | registration framing | use common-area framing or deliberate crop; do not darken the background to hide seams |
| large-scale gradient | background extraction, after calibration is validated | adjust polynomial degree/RBF sampling and tolerance; mask or exclude the object region if supported |
| background clipped to black | stretch/background extraction | reduce shadows clipping or stretch strength; restore prior checkpoint |
| galaxy/nebula core blown out | stretch | increase highlight protection or use a gentler multi-step stretch |
| green/magenta cast | color calibration/background neutrality | verify plate solve/PCC first; use green removal only for a measured residual |
| gray or neon color | color/saturation | change saturation modestly; do not compensate for failed calibration |
| comet expected cyan but rendered gray/brown | PCC/SPCC or automatic green removal | require log-proven calibration, correct BGE first, disable green removal, then adjust saturation modestly |
| plastic or blotchy texture | denoise | lower modulation, disable booster, or revert; inspect at 100% |
| dark halos or crunchy stars | sharpening/deconvolution | lower amount/radius or disable; return to the pre-sharpen checkpoint |
| enlarged pale stars | stretch/saturation | protect highlights and reduce late saturation; avoid aggressive unlinked stretch |

## Stage checks

### Linear stack

Look for consistent round stars across the field, clean registration, stable background, absence of channel mosaicing, and no systematic calibration residue. A visually poor linear stack invalidates downstream tuning.

### Background extraction

Compare corners and central background without expecting perfectly black sky. Reject attempts that remove real galaxy halos, integrated flux nebulosity, or wide dust. A higher polynomial degree is not automatically better. RBF needs enough clean samples and conservative smoothing.

### Color

After plate solving and PCC/SPCC, expect plausible stellar color diversity and a near-neutral empty background. Verify the command succeeded in the log. Do not accept a visually attractive but failed calibration as photometric.

### Denoise

Inspect blank background, faint arms/nebulosity, and small stars. Prefer visible fine-grained noise over waxy structure, blocks, color smears, or invented filaments. Judge at the final display scale and at 100%.

### Star separation and reconstruction

Inspect the starless and star-mask layers separately. The starless layer must retain comet coma/tail, galaxy cores, and compact nebular detail; the star mask must not contain those structures. Before recomposition, remove any comet nucleus residual from the star mask with a soft local protection mask. After recomposition, reject doubled nuclei, star holes, black rings, clipped colored cores, and halos. Compare the recomposed image to the pre-separation calibrated checkpoint to ensure the operation changed prominence rather than identity or position.

### Stretch

Check the histogram statistics, background visibility, brightest stellar cores, galaxy nucleus, and faint outer structure. Preserve a meaningful black point without crushing low-signal data. Linked stretches are the default after color calibration.

### Finish

Use restrained saturation and sharpening. Stars should retain colored cores where data allow, not become uniformly white or surrounded by black rings. Background chroma noise should not be amplified.

## Iteration discipline

1. Save every attempt's parameters, log, checkpoints, and preview.
2. Name the observed defect before changing a parameter.
3. Change one causal parameter family at a time; two values may move together only when they define one operation, such as stretch amount and highlight protection.
4. Use the earliest still-valid checkpoint as the retry input.
5. Retain a best-so-far attempt and compare against it.
6. Use quantitative ties—background median/noise, clipping fraction, registration count—only when visually appropriate.
7. Stop a stage after three consecutive non-improving attempts or 15 attempts, whichever comes first.
8. Do not cap total attempts globally; complex data may legitimately revisit an upstream stage.
9. Give a progress update at least every 30 minutes during a long run.
10. Escalate aesthetic ties to the user with the two best previews and a concrete tradeoff.

## Final acceptance

Open the chosen PNG and JPEG, not only the Siril preview. Confirm:

- no unexpected orientation or crop change
- no clipped nucleus or dominant star cores
- no crushed faint structure
- neutral, non-banded background
- stable star shapes across the frame
- natural noise texture at intended display scale
- TIFF/PNG/JPEG match the selected processed FITS
- output filenames and dimensions are correct

Select an attempt only after these checks pass or after the user explicitly chooses a stated aesthetic compromise.
