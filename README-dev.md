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

`sima-cli models download` supports both archive packages and deployments stored
as individual registry artifacts. Folder deployments are downloaded directly to
`<output>/<model_id>/<build_profile_id>/`, preserving their `devkit/`,
`elf_files/`, `npy_files/` and other directories. No extraction step is needed.
Existing archive packages retain their single-file download behavior.

Catalog packages and `approved_deployments` select their exact original runs and
artifacts, including builds inherited or approved from another branch. Run
metadata is fetched with up to four parallel requests. Approved full-package roots
take precedence over their component roots; a standalone component root is also
supported. Source branch and commit provenance are preserved.

#### Publisher contract

A completed package run exposes a `model-package` root; a standalone component
uses a `model-pack` root. Set its media type to `application/json` for a folder deployment.
Its contents are the package manifest, for example:

```json
{
  "model_id": "qwen3_5_2b",
  "build_profile_id": "w4a16",
  "package_id": "default",
  "files": [
    {
      "path": "devkit/vlm_config.json",
      "artifact": "file-2887acbfe6933c7dcedb59ff82c18b08c1e7e9b5e694c5355513126587e86cfb",
      "size_bytes": 4096,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ]
}
```

Each file references an available artifact registered on that same package run.
`artifact` is `file-` followed by the SHA256 of its relative POSIX `path`.
Register it as `model-file` with `metadata.path` equal to that path.
Size and SHA256 must match the registered artifact. Other package metadata,
including provenance and license, can remain in the manifest. Model, build profile
and package identity must match the selected run; package ID is checked when the
run declares it.

Paths must be unique, including case-insensitive comparisons, and cannot contain
absolute paths, traversal, backslashes, drive prefixes or file/directory conflicts.
Path components cannot end in a dot or space, which can alias other paths on Windows.
Reserve root `manifest.json` for the downloaded manifest. Temporary transfer
files are kept outside the deployment tree.

The publisher uploads/registers the files and manifest before finalizing the
package run. Keep signed URLs out of the manifest: the CLI obtains them from
`GET /v1/runs/{run_id}/artifacts/{artifact_name}` when each transfer begins.

#### Download behavior

The CLI verifies the manifest, validates every file entry, and processes up to four
files concurrently, scheduling larger files first. Before downloading a file, it
checks existing and retained files by size and then SHA256. Verified files are
reused without a signed-URL request or a download. Hard links avoid extra copying
where supported; other filesystems fall back to copying.

New transfers stream to disk and verify size and SHA256 in the same pass. Expired
signed URLs are refreshed once. Interactive byte/file progress uses stderr.
Cancellation signals workers to stop and closes active streaming responses; a
pending network operation can still wait for its I/O timeout.

The sibling `.<build_profile_id>.download/deployment/` directory retains verified
files after failure or cancellation. Retrying verifies those files again and
fetches only missing or invalid ones. Partial files restart from the beginning;
there is no HTTP byte-range resume. A changed manifest resets the retained staging
directory. The destination remains untouched until the complete deployment
verifies. Repeating the same deployment repairs missing or corrupt files without
`--force`; a different or unrecognized existing deployment requires `--force`.

Installation replaces the complete directory, removing obsolete files rather than
merging builds. A failed final rename restores the previous deployment. Cleanup
failures after successful installation produce a warning. Symlink and
non-directory destinations are rejected even with `--force`. A sibling lock file
prevents concurrent downloads to the same destination and remains after completion.

For folder downloads, `--json` reports `format: "directory"`, `path`, `file_count`,
`size_bytes` (deployment files, excluding the manifest), `reused_files`,
`downloaded_files`, and `manifest_sha256`. Archive download result fields are
unchanged.

---

### 🔗 Related Links

- [SiMa Developer Portal](https://community.sima.ai/)
