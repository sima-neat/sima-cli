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
`install_args` (CLI arguments with optional `{sdk_version}` and `{arch}` placeholders).
`bundle_sdk_version` specifies the SDK version declared by compatible offline ZIPs
(the 2.1.3.1 online artifact retains the 2.1.3 bundle version).
SDK versions retain every numeric component from `/etc/sdk-release`.

SDK 3.0.0 and 3.0.0.0 select Model Compiler 3.0.0; SDK 3.0.0.1 selects
Model Compiler 3.0.0.1 through `neat install`. Version comparisons treat
trailing zero components as equivalent, so `==3.0.0.0` also matches 3.0.0.
Other SDKs at or above 2.1.3 retain the historical compiler 2.1.3.1 pin; SDKs below 2.1.3
use their own version through the legacy installer. Unlisted future releases
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
