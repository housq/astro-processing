# PixInsight adapter policy

PixInsight is an experimental main backend until each stage passes a real PJSR smoke test on the detected version and platform.

Use PJSR/JavaScript as the reproducible interface. Discover the supported launch mechanism from the installed PixInsight version; do not invent a universal headless CLI. Treat GUI automation as an exceptional fallback, not the primary adapter.

Delivery order:

1. Post-integration PJSR: ABE/DBE, SPCC, stretch, checkpoints, and export.
2. Cross-software FITS boundary validation.
3. Full WBPP calibration, registration, integration, and XISF provenance.

Preserve XISF inside the PixInsight main workflow. Use 32-bit FITS only for an explicit boundary to GraXpert, StarNet, RC-Astro, Siril, or final interoperability output. Discover installed processes and scripts during preflight. Treat third-party modules and RC-Astro products as separate licensed capabilities.

Do not automatically select PixInsight merely because its application exists. Require a successful execution smoke test and stage-level maturity. If the user explicitly requires an installed experimental PixInsight path, explain the maturity in the consolidated preflight and confirm once before execution.
