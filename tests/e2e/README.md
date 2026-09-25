# End-to-end tests

End-to-end tests exercise the CLI through its command entrypoints instead of
calling implementation helpers directly. Keep them focused on behavior that unit
tests cannot catch easily: packaging/import issues, command registration,
subprocess execution, filesystem side effects, Docker integration, device
networking, or authenticated services.

## Local-only smoke tests

Local-only e2e tests must run without Docker, device hardware, credentials, or
network access. They should isolate all persistent state under temporary
directories and set `SIMA_CLI_CHECK_FOR_UPDATE=0` so the CLI does not contact
PyPI during the test.

Run the safe local-only suite with:

```bash
./scripts/run-tests.sh e2e -m local_only
```

The local smoke tests are intended to be safe for a normal laptop development
loop and for CI jobs that do not have access to SiMa hardware.

## Opt-in external tests

Use more specific markers for tests that need external state:

| Marker | Use when the test requires |
| --- | --- |
| `docker` | Docker daemon, SDK images, or containers |
| `device` | A reachable SiMa DevKit or device network |
| `credentials` | Developer Portal, Artifactory, registry, or other authenticated services |

Run the full e2e directory explicitly with:

```bash
./scripts/run-tests.sh e2e
```

Run only Docker-backed tests:

```bash
./scripts/run-tests.sh e2e -m docker
```

Run only device-backed tests:

```bash
./scripts/run-tests.sh e2e -m device
```

## Adding e2e tests

Prefer local-only tests whenever possible. For commands that usually touch real
systems, use temporary fixtures and non-destructive command modes before adding
Docker, credential, or device requirements.

Every e2e test should:

- mark itself with `pytest.mark.e2e` and at least one capability marker
- isolate `HOME`, `SIMA_CLI_HOME`, and other CLI state under `tmp_path`
- disable automatic update checks with `SIMA_CLI_CHECK_FOR_UPDATE=0`
- avoid writing to the developer's real `~/.sima-cli`
- document required external setup when using `docker`, `device`, or
  `credentials`
