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

### Model deployment folders

`models download` uses the exact deployment selected by the branch catalog,
including inherited and approved builds. Existing archive packages still download
as single files. Folder deployments preserve their layout under
`<output>/<model_id>/<build_profile_id>/`, ready to use without extraction.

For publishers:

- Register the root as `model-package` (or `model-pack` for a standalone component)
  with media type `application/json`.
- The manifest contains `model_id`, `build_profile_id`, `package_id` and `files`.
  Each file specifies `path`, `artifact`, `size_bytes` and `sha256`.
- Register each file on the same run as `model-file`, named
  `file-<SHA256(relative POSIX path)>`, with `metadata.path` set to that path.
  Manifest identity, sizes and checksums must match the registered artifacts.
- Use safe, unique relative paths; reserve root `manifest.json` for the manifest.
  Register all files before finalizing the run. Signed URLs are fetched at download
  time and do not belong in the manifest.

Downloads use up to four parallel transfers and reuse files verified by size and SHA256.
Retries retain completed files; partial files restart. The previous deployment
remains available until the replacement verifies and installs successfully.
Repeating a download repairs missing or corrupt files; replacing a different
build requires `--force`. JSON output includes the destination, total size and
file counts for reused and downloaded files.

---

### 🔗 Related Links

- [SiMa Developer Portal](https://community.sima.ai/)
