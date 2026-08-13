# SETI Astro Suite Pro adapter

Use the official `setiastrosuitepro cc` or `cosmicclarity` CLI only after discovery proves that its headless help exposes the expected commands and the stage's local model sentinels exist. Treat every version/platform/stage combination as experimental until smoke-tested. Installation and model downloads require the user's consolidated approval; never let first execution trigger an implicit model download.

## Deliberately narrow boundary

Route only:

- `detail_restoration` through Cosmic Clarity `sharpen`;
- `satellite_removal` through Cosmic Clarity `satellite`.

Keep SETI Astro background extraction, color calibration, stacking, and all other main-flow operations disabled. Do not expose or default to super resolution: it changes dimensions and can synthesize detail, so it requires a separate future contract and evaluation.

Allow `denoise` and DarkStar `star_separation` only with the explicit `--ab-candidate` flag. These candidates must be compared with the frozen route on the same input. Accepting an A/B candidate records a preference but does not promote it; confirm and freeze a new route first. DarkStar's current file-level CLI saves starless output but not a separately addressable star layer, so it cannot satisfy the operational star-separation contract by itself.

## Conservative commands

The adapter constructs the installed CLI prefix and never invokes a shell. Representative commands are:

```text
setiastrosuitepro cc sharpen -i input.fit -o output.fit --gpu \
  --chunk-size 256 --overlap 64 --temp-stretch \
  --sharpening-mode "Non-Stellar Only" \
  --stellar-amount 0.25 --nonstellar-amount 0.35 --nonstellar-psf 3

setiastrosuitepro cc satellite -i input.fit -o output.fit --gpu \
  --chunk-size 256 --overlap 64 --mode full --clip-trail --sensitivity 0.1
```

Use temporary stretch only for linear sharpening/denoise. Inspect fine structures and star edges at 100% for ringing, false filaments, or plastic texture. For satellite removal, first prove a residual trail exists after integration rejection; reject the candidate if real nebulosity, galaxy structure, diffraction spikes, or comet tails are altered.

The command contract was verified against the official [SETI Astro Suite Pro repository](https://github.com/setiastro/setiastrosuitepro) `src/setiastro/saspro/cli.py`. The software is free to download with a suggested donation; verify the installed package and bundled model licenses separately before use.
