# PixInsight 1.9.3 constrained PJSR evidence

Representative workflow date: 2026-08-13. Final adapter smoke date: 2026-08-18. Attempt/review regression date: 2026-08-18. Paths and target-identifying filenames are sanitized. No input frame, full-size result, model, binary, license material, username, or absolute home path is stored here.

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

## Isolated attempt and visual-review regression

The revised adapter ran a fresh synthetic smoke and an authenticated RC-Astro capability probe on 2026-08-18. A representative 1554×1034 full-field downsample of a real integrated OSC Float32 stack then completed the consolidated ABE → classic color → BXT → SXT → NXT → layer finish → export route in isolated attempts. The first technical success entered `needs_review` and was visually rejected because the stellar field dominated the faint target. A tuning file changed only the allowlisted layer finish parameters; the second execution used a distinct directory and execution ID, entered `needs_review`, and was accepted after preview inspection. A later diagnostic-preview attempt was rejected because its linked ABE model stretch was visually unusable. The final attempt used an unlinked display-only diagnostic stretch, produced reviewable ABE corrected/model, starless/stars, and final previews, and was accepted. Only acceptance changed the run to `complete`; the final selected attempt is the last one.

All four attempts generated and reopened XISF, 32-bit FITS, 32-bit TIFF, 8-bit PNG, and 8-bit JPEG at 1554×1034×3. The input had no WCS; the checks verified that WCS absence, dimensions, channels, bit depth, declared linear/nonlinear state, and orientation remained consistent. Sanitized state, parameters, output metadata, probe results, review findings, and limitations are in `pixinsight-review-regression-summary.json`. ABE corrected/model, RC starless/stars, rejected final, and accepted final previews were reviewed locally. Their observation-derived pixels are intentionally not committed.

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

The captured PixInsight runs predate the final Python-side frozen-contract, concurrency-lock, and strict-manifest hardening. Those gates have dependency-free regression coverage, but this exact revision still needs a fresh PixInsight-host synthetic smoke plus representative visual run before its runtime evidence can be considered current.

WBPP itself, calibration, registration, integration, DBE, SPCC, automated ImageSolver, external FITS round trips, other OS/architectures, other PI builds, and other data types were not validated. ImageSolver was completed manually in the GUI in the representative workflow and is not claimed as automated. RC-Astro licenses were confirmed by the user; no license information is recorded.
