# Sentinel catalog snapshots

These files are **recordings** of what SiMa Sentinel's `GET /v1/peripherals` returned, kept so the
contract tests (`tests/unit/test_peripherals_contract.py`) can run the CLI over real output. They
define nothing: what a board supports is defined by Sentinel, whose API and record shapes are
documented in the `sentinel` repository (`docs/api.md`, `docs/peripherals/*.md`). The unit tests
do not read these files; they build the smallest records they need from
`tests/unit/peripherals_fixtures.py`.

| File | What it is | Captured | Provenance |
| --- | --- | --- | --- |
| `catalog-example.json` | Sentinel's own documented example catalog: a Modalix DevKit on Platform 2.1.3 with an IMX477 (MIPI), a Logitech C920 (USB) and the C920's microphone. The ISP has a fixed size table (`sizing: fixed`). | 2026-10-05 05:12 UTC | Copied verbatim from the `sentinel` repository, `docs/peripherals/catalog-example.json` at `origin/main` `2b03605` (the file last changed in `1f4f964`, 2026-10-09). Refresh it from there; do not edit it here. |
| `devkit-3.0-B1859.json` | The same three devices on Platform 3.0.0 (`3.0.0_develop_B1859`), where the ISP sets sizes at run time (`sizing: runtime`) and the sensor's sizes are listed (`sensor_mode`). 55 IMX477 modes, 35 C920 modes. | 2026-10-08 23:05 UTC | Recorded on a Modalix DevKit at `192.168.2.2` running a Sentinel built from the `feat/40-cross-version` branch (PR sentinel#43) with `curl --unix-socket /run/simaai-sentinel/api.sock http://sentinel/v1/peripherals`. |

Refreshing a snapshot is a deliberate change: record the new file, update this table, and expect
the contract tests to tell you what changed.
