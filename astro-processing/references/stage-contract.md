# Shared stage contract

Use the common semantic stages without forcing identical commands or intermediate formats:

```text
inspect → calibrate → clean → register → integrate
→ background extraction → color calibration → deconvolution
→ star separation → denoise → stretch → star reconstruction → export
```

Hard constraints:

- Calibration masters and calibrated lights remain linear.
- Background extraction precedes PCC/SPCC unless a documented implementation requires otherwise.
- Deconvolution/BXT requires linear input and runs before denoise, star separation, and stretch.
- Star separation retains both starless and star layers and records target leakage checks.
- Linear-only tools never run after stretch.
- PCC/SPCC requires actual success evidence; process exit alone is insufficient.
- Never apply automatic green removal after successful PCC/SPCC without a documented residual cast.
- Final export follows visual QC, not merely command completion.

Use native formats inside a main backend:

- Siril: 32-bit FITS.
- PixInsight: XISF.
- Cross-software exchange: explicit 32-bit FITS checkpoint.

At every boundary validate width, height, channels, sample/bit depth, linear state, WCS, orientation, and representative pixel statistics. Record any metadata that cannot survive the exchange. Do not mix Siril and PixInsight main stages unless the user explicitly approves the switch and boundary.
