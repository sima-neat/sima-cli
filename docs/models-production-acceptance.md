# Production Model Registry consumer acceptance

Validated on October 23, 2026 (America/Los_Angeles) from the issue #326
implementation source. Package metadata is `2.1.20`; this is a local build,
not evidence that a released client includes the change. Released CLI version
containing this change: **pending release**. The implementation PR targets
`develop`; release validation remains a follow-up after merge and publication.

## Live read-only acceptance

No AWS credentials or endpoint overrides were supplied to the models client.
Production endpoint: `https://models-registry.neat.sima.ai`.

- Branch listing returned `codex/production-onnx-canary` and
  `codex/production-resnet50-canary`.
- Published catalog: `codex/production-resnet50-canary`, with `resnet_50`
  variants `w16a16` and `w8a8`.
- Selected package run: `1ff7bcb9-9f36-42d3-a957-3168ab188409` (`w8a8`).
- CLI model-detail path successfully read summary, provenance, test results,
  metrics, benchmark and accuracy information for the selection.
- `models download --branch codex/production-resnet50-canary --id resnet_50
  --variant w8a8 --output <temporary-directory> --json` succeeded using the
  default production endpoint.
- Artifact: `resnet_50_w8a8_mpk.tar.gz`, **22,088,659 bytes**.
- SHA-256: `426975ac8b98c99e258746e9d8a68d8f1db5b0d4f20c2769266699088c803b76`.
  The CLI verified both size and digest before installation; an independent
  local byte count and SHA-256 computation also matched.
- Production `main`, `develop`, and the ONNX canary had no published catalog
  (HTTP 404). The client reported the selected branch and endpoint without
  falling back. A registered branch does not guarantee a published catalog.
- Staging regression: `https://models-registry.stg.neat.sima.ai` returned
  45 branches and 43 model selections from the `develop` catalog.

## Scope and validation

`models` remains hidden in top-level help and directly invokable. `modelzoo`,
producer `VULCAN_ENV` values, approvals, and private-artifact access are unchanged.
The unit suite covers endpoint precedence, staging aliases, empty/unpublished
catalogs, API failures, verified downloads and existing download safeguards.
`./build.sh` successfully generated documentation, checked Python compatibility,
and built the wheel and source distribution. Generated command documentation has
no diff because its generator skips hidden command groups.

Before claiming released-client availability, record the release version and
repeat the production download with that installed release. These canary names
and catalog contents are observations at validation time, not stable guarantees.
