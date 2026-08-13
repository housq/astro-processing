# Routing and configuration

## Configuration layers

Apply values from lowest to highest priority:

1. skill defaults;
2. `~/.config/astro-processing/preferences.yaml`;
3. `<project>/.astro-processing/project.yaml`;
4. `<project>/.astro-processing/project.local.yaml`;
5. current request and CLI arguments.

Allow `project.yaml` in Git. Put machine paths and local-only state in ignored `project.local.yaml`. Do not write a long-term preference when a user merely confirms one run. Write it only after an explicit instruction such as “always prefer PixInsight”.

Example:

```yaml
schema_version: 1
routing:
  preferred_main_backend: siril
  allow_mixed_main_backends: false
execution:
  profile: balanced
processors:
  background_extraction:
    mode: prefer
    candidates: [graxpert, main]
  star_separation:
    mode: prefer
    candidates: [sxt, starnet, main]
behavior:
  confirm_first_route: true
```

## Confirmation boundary

Preflight before expensive processing. Consolidate blockers and degradations into one question. A healthy first route still requires one confirmation. Later identical routes may notify and proceed after a future implementation can prove the environment and route fingerprint are unchanged.

Do not ask again for ordinary parameter retries or a recorded fallback. Ask again for:

- installation or large model download;
- paid license activation;
- main-backend switch;
- abandoning a hard requirement;
- materially higher time/disk use;
- destructive cleanup;
- an aesthetic tie without an objective winner.

## Modes

- `require`: the first named tool is mandatory; stop if unavailable.
- `prefer`: try named tools in order, then recorded validated fallbacks.
- `auto`: use profile defaults and validated capabilities.
- `disabled`: skip the stage.

Environment presence is not capability. Require executable, compatible version, model/weights, active license where relevant, and validated stage maturity. Record the actual selection, source, maturity, version, path, fallback chain, and fallback reason in the run snapshot.
