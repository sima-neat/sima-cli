# sima-cli

`sima-cli` is a lightweight command-line tool to interface with the SiMa Developer Portal. It allows users to authenticate, download models and firmware, and manage updates for SiMa devices.

## 🔧 Features

- Login with browser-based or manual authentication.
- Download resources via full URL or URI.
- List and download models and apps.
- Update firmware from version or URL.
- Automatically detects board vs PCIe host environment.

---

## 💻 For Developers

### 📁 Project Structure

```
sima-cli/
├── sima_cli/          # CLI source code
│   ├── cli.py         # Main CLI entry point
│   ├── __version__.py # Version string
│   ├── auth/          # Authentication logic
│   ├── download/      # Download logic
│   ├── update/        # Firmware update logic
│   ├── model_zoo/     # Model zoo interactions
│   ├── app_zoo/       # App zoo interactions
│   ├── install/       # Component installation (hostdriver, optiview, metadata)
│   ├── sdk/           # SDK container management
│   ├── deploy_only/   # Device and MPK management
│   │   ├── device/    # Device connection and lifecycle
│   │   └── mpk/       # MPK package deployment
│   ├── storage/       # NVMe and SD card utilities
│   ├── network/       # Network configuration (board-side)
│   ├── serial/        # Serial console access
│   ├── discover/      # Device discovery
│   ├── mla/           # MLA memory telemetry
│   ├── peripherals/   # DevKit peripheral discovery, read from SiMa Sentinel
│   ├── upgrade/       # Self-update mechanism
│   ├── data/          # Embedded configuration files
│   └── utils/         # Environment detection, config, APIs
├── tests/
│   ├── unit/          # Fast tests with mocked external dependencies
│   └── e2e/           # Opt-in tests that require real containers/devices/services
├── pyproject.toml     # Build config (PEP 517/518)
├── setup.cfg          # Package config
├── setup.py           # Dynamic setup script
├── requirements.txt   # Dependencies
└── README.md          # User documentation
```

---

### SDK release mapping

`sima_cli/sdk/releases.json` is the source-controlled SDK-to-Model-Compiler
mapping bundled in both source and wheel distributions. `schema_version` versions
the file format; the resolver currently supports version 1.

The `model_compiler.releases` list uses **first-match order**. Add explicit SDK
release entries before the historical fallback rules. Each entry specifies
`sdk_versions` (an exact version or comma-separated comparison constraints) and
`install_args` (CLI arguments with optional `{sdk_version}`, `{legacy_sdk_version}`,
and `{arch}` placeholders). `{legacy_sdk_version}` retains the first three numeric
components for older compiler releases.
`bundle_sdk_version` specifies the SDK version declared by compatible offline ZIPs
(the 2.1.3.1 online artifact retains the 2.1.3 bundle version).
SDK versions retain every numeric component from `/etc/sdk-release`.

SDK 3.0.0 and 3.0.0.0 select Model Compiler 3.0.0; SDK 3.0.0.1 selects
Model Compiler 3.0.0.1 through `neat install`. Version comparisons treat
trailing zero components as equivalent, so `==3.0.0.0` also matches 3.0.0.
Other SDKs at or above 2.1.3 retain the historical compiler 2.1.3.1 pin; SDKs below 2.1.3
use their first three version components through the legacy installer and for
offline ZIP compatibility (for example, SDK 2.1.2.3 uses compiler/bundle version
2.1.2). Unlisted future releases
also follow that historical fallback until an explicit mapping is added.
Architecture aliases and SDK constraints live in `model_compiler.architectures`:
AMD64 retains legacy support, while ARM64 requires SDK 2.1.1 or later.
Package metadata compatibility checks still apply during installation.

Run `python -m pytest tests/unit/test_sdk_release_mapping.py tests/unit/test_sdk_image_detection.py`
after updating the mapping.

### 🏗 Build Instructions

#### 1. Install dev dependencies

```bash
python3 -m pip install --user virtualenv  # one-time
python3 -m virtualenv venv
source venv/bin/activate
pip install -r requirements.txt -r dev-requirements.txt
```

> ℹ️ If your shell cannot find `virtualenv` or `pip` after the user install, export `PATH="$HOME/.local/bin:$PATH"` before running the commands.  
> If your system Python includes `ensurepip`, you can fall back to `python3 -m venv venv`.

#### 2. Install package locally

```bash
pip install -e .
```

#### 3. Run CLI tool

```bash
sima-cli help
```

---

### 🧪 Run Tests

```bash
./scripts/run-tests.sh
```

The default suite is `unit`, which is suitable for a local macOS development
loop and CI presubmit jobs. End-to-end tests are explicit:

```bash
./scripts/run-tests.sh unit
./scripts/run-tests.sh e2e
./scripts/run-tests.sh compat
./scripts/run-tests.sh all
```

Extra arguments are passed through to pytest:

```bash
./scripts/run-tests.sh unit -q
```

The compatibility suite builds and installs a `sima-cli` wheel on any installed
Python interpreters from 3.8 through 3.14, then validates the installed package
and CLI version. Missing interpreters are skipped locally; CI can require the
full matrix with:

```bash
./scripts/run-tests.sh compat --strict
```

On macOS, install missing compatibility interpreters with pyenv:

```bash
brew install pyenv
./scripts/run-tests.sh compat --install-missing --strict
```

CI passes the wheel built by the build job into the compatibility suite:

```bash
./scripts/run-tests.sh compat --strict --wheel dist/sima_cli-*.whl
```

---

### 📦 Build and Publish to PyPI

```bash
# Build wheel and sdist
python -m build

# Upload using Twine
twine upload dist/*
```

> ⚠ Make sure your version number is updated in `setup.cfg` or `pyproject.toml` before release.

---

### 🔗 Related Links

- [SiMa Developer Portal](https://community.sima.ai/)

### Artifact request resilience

The direct-file downloader uses a 10-second connection timeout and a 60-second
read-inactivity timeout for its preliminary HEAD and sequential GET requests.
Before response headers arrive, connection/read timeouts and connection failures
are retried up to three total attempts with 1- and 2-second backoff. HTTP 429,
500, 502, 503, and 504 responses also receive these bounded retries. Other HTTP
errors and TLS certificate errors fail immediately.

These retries do not replay an interrupted response body or change the separate
parallel-download path. Existing partial-file validators, resume checks, and
atomic publication continue to protect downloaded artifacts.

### CloudEx browser login and allocation

CloudEx separates external Arthas access from internal Vulcan access. Without
`-i`, `cloudex login` uses the Developer Portal, and `cloudex logout` clears only
Developer Portal credentials. External CloudEx device operations report that
Arthas support is not available yet; they never use Vulcan credentials or sessions.

Use `sima-cli -i cloudex ...` (or `sima-cli cloudex -i ...`) for internal Vulcan
production. `SIMA_CLI_INTERNAL=1` also selects internal access. Internal commands
that need NeatOps automatically open browser login if you are signed out or your
saved login has expired, then continue the original command. Valid credentials
are refreshed silently. Permission failures and service outages do not start a
new login. `cloudex login` is also available explicitly. Examples:

```bash
sima-cli -i cloudex login
sima-cli -i cloudex devices
sima-cli -i cloudex request --duration 4h
# Or choose a specific available device:
# sima-cli -i cloudex request --device DEVICE_ID --cluster APPLIANCE_ID --duration 1h
sima-cli -i cloudex connect --allocation ALLOCATION_ID
sima-cli -i cloudex release ALLOCATION_ID
sima-cli -i cloudex logout
```

Use `-i` together with `--environment staging` to select Vulcan staging.
Production and staging credentials are stored separately. Login opens the browser on the same computer as the CLI and
waits up to three minutes for the loopback callback on `127.0.0.1:47182`.
Authentication uses authorization code with PKCE and the dashboard's active
Cognito app client and SSO provider. The CLI stores tokens in a mode-0600 file
under `~/.sima-cli/cloudex/`, refreshes them when needed, and never prints them.

Request checks your lease limits, offers available devices, reserves the chosen
device, waits up to three minutes for preparation, and connects through the
existing CloudEx forwarder. If preparation or connection fails, the allocation
ID is printed for inspection, reconnection, or release. A failed or ambiguous
request is never automatically retried with a new allocation ID. Disconnecting
a tunnel and logging out retain the device lease; `release` ends it and starts
server-side cleanup. Existing allocation-key connection commands still work.

The paired Vulcan change publishes `/cli-config.json` and registers the exact
loopback callback on both dashboard OAuth clients. Deploy that change before
using CLI login; no credential or mutable client ID must be copied into the CLI.

### SDK-managed editor components

For code-server SDKs, setup detects `/usr/local/bin/sdk-components` and queries
`sdk-components resolve --json` inside the container as the mapped user. Schema 1
returns the SDK release, cohort, qualification status, source, architecture,
editor engine versions, and exact managed extension versions. Setup displays
these pins and delegates each selected agent to `sdk-components install --json
--extension ID@VERSION --extensions-dir /absolute/path`. The SDK rechecks the
catalog at installation and verifies artifact identity/checksum, installed version,
and persistent update pin. sima-cli does not edit pins for these managed agents.

Existing qualification-aware SDK images do not need this new interface. If it
is absent, setup detects the shipped code-server wrapper and passes bare managed
IDs to it. Exact user overrides are passed through for the SDK to approve or
reject; `@latest` selects the SDK-approved version, not the gallery's latest.
The wrapper owns catalog selection and artifact verification. Setup then reads
the editor profile to verify the installed version and persistent pin and reports
the installed version. It does not skip an existing install or overwrite SDK pins.
Exact qualified versions are shown before installation only when the structured
resolver is available; shipped wrappers report the resolved version afterward.

Neither managed path uses CLI compatibility pins. SDKs without qualification
support retain the older extension-manifest/compatibility behavior. Missing
wrapper files in a qualification-aware image, or an unavailable, incompatible,
revoked, malformed, or unsupported resolver response are errors, including during
offline setup when no compatible cached data exists. Only explicit SDK
candidate-test settings permit candidate installation through the shell
interface; setup does not enable them or the panel's fallback policy.

Resolution and installation failures stop setup with a nonzero exit status and
retain the SDK container for repair. Installer stdout/stderr is preserved, and
setup does not print its final success message. Selected Studio installation
failures also stop setup. Qualification failures are not retried by sima-cli;
legacy installers retain their existing transient retry behavior.
