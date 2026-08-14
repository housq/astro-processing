# PixInsight 1.9.3 constrained PJSR evidence

Representative workflow date: 2026-08-13. Final adapter smoke date: 2026-08-14. Paths and target-identifying filenames are sanitized. No image data, model, binary, license material, username, or absolute home path is stored here.

## Environment and launch

- Host: macOS 15.6.1 (24G90), arm64.
- PixInsight application: Core 1.9.3 build 1646; executable is x86_64 on this arm64 host.
- Launch shape: `<PI_APP>/Contents/MacOS/PixInsight --execute=<RUN>/scripts/pixinsight-pipeline.js`.
- Mode: IPC to an already-running GUI, not headless.
- Success gate: unique result JSON has `ok: true` and `successMarker: ASTRO_PROCESSING_PIXINSIGHT_OK`.

## Representative real OSC workflow

Input was an existing post-WBPP, integrated, linear OSC color XISF. It was 6219×4139, RGB, Float32. The source was not modified.

Stages and parameters actually completed:

- ABE subtraction: degree 1, tolerance 1.0, deviation 0.8, box size/separation 10, downsample 2.
- BackgroundNeutralization: range 0–0.10, target background 0.001.
- Classic ColorCalibration: structure detection, 5 structure layers, 1 noise layer, white range 0–0.90. SPCC was explicitly skipped.
- Native branch: linear MLT denoise; linked stretch at midtones 0.005254792191279071; saturation +0.08.
- RC branch: BXT 2.1.4 AI4, `correct_first=false`, automatic PSF, stars 0.30, nonstellar 0.35, halos 0.
- SXT 2.4.11 AI11 lite nonoise, stars retained, `unscreen=false`, overlap 0.20.
- NXT 2.3.3 AI3 on starless linear data only: denoise 0.55, color 0.65, 2 iterations, detail 0.20.
- Layer finish: starless/stars midtones 0.0045/0.012, saturation +0.12/+0.04, screen reconstruction, star strength 0.70.

Key success evidence:

- Every stage report recorded `ok: true`.
- SXT reconstruction check recorded zero median residual in all channels.
- Final XISF reopened as 6219×4139×3, Float32, with astrometric solution retained.
- TIFF reopened as 6219×4139×3 Float32; PNG and JPEG reopened as 6219×4139×3 UInt8. WCS was not retained in delivery formats.
- The RC-Astro linear XISF checkpoints remained declared linear through NXT; final XISF/TIFF/PNG/JPEG were declared non-linear after HistogramTransformation.
- Final visual QC covered fit-to-screen plus center/corners at 100%.

Generated artifact classes were XISF checkpoints/final, TIFF, PNG, and JPEG. The new synthetic adapter smoke additionally generated and reopened linear Float32 XISF/FITS/TIF and 8-bit PNG/JPG. Artifact bytes are intentionally excluded from git. See `pixinsight-smoke-summary.json` for the sanitized machine-readable summary.

## Reproduction checks

The final change was checked with:

```text
python3 astro-processing/tests/test_router.py
python3 astro-processing/tests/test_pixinsight.py
python3 -m compileall -q astro-processing/scripts astro-processing/tests
python3 -m json.tool astro-processing/references/evidence/pixinsight-smoke-summary.json
PYTHONPATH=<TEMP_PYYAML> python3 <SKILL_CREATOR>/scripts/quick_validate.py astro-processing
git diff --check
```

All five dependency-free test programs passed every case after rebasing onto the latest `main`; compilation and JSON parsing passed, the skill validator returned `Skill is valid!`, and `git diff --check` returned no errors. The temporary PyYAML copy was used only to run the validator and is outside the repository.

## Limits

WBPP itself, calibration, registration, integration, DBE, SPCC, automated ImageSolver, external FITS round trips, other OS/architectures, other PI builds, and other data types were not validated. ImageSolver was completed manually in the GUI in the representative workflow and is not claimed as automated. RC-Astro licenses were confirmed by the user; no license information is recorded.
