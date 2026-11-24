#!/usr/bin/env python3
"""Build the per-user sima-cli Windows installer (MSI) from a built sima-cli wheel.

The MSI carries the python.org embeddable CPython, sima-cli and its pinned dependencies as
plain files, with bin\\sima-cli.exe as the only folder on PATH.

Stages 1-8 stage the bundle and run on any system (--stage-only stops after them):
  1. fetch     the embeddable CPython zip; its SHA-256 must match the pin
  2. runtime   extract it to bundle\\runtime and enable `import site`
  3. packages  install the pinned win_amd64/cp314 wheels and the sima-cli wheel
  4. check     every requirement of sima-cli and of what it needs is bundled, for Windows x64
  5. marker    sima-cli's INSTALLER file names the MSI, so the CLI points updates at WinGet
  6. compile   .pyc next to every .py, so Python never writes into the install folder
  7. launcher  bin\\sima-cli.exe, the distlib launcher pip writes, with a launcher-relative shebang
  8. paths     no bundle path is longer than MAX_BUNDLE_PATH characters
Stages 9-11 need Windows and WiX 3 (heat, candle, light):
  9. smoke     bin\\sima-cli.exe --version prints the wheel's version
 10. msi       harvest the bundle, compile and link sima-cli.wxs
 11. record    <msi>.sha256 and installer.json, which winget_manifest.py reads
"""

from __future__ import annotations

import argparse
import configparser
import email.parser
import hashlib
import importlib.util
import io
import json
import os
import py_compile
import re
import runpy
import shutil
import subprocess
import sys
import urllib.request
import uuid
import warnings
import zipfile
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WXS = HERE / "sima-cli.wxs"
REQUIREMENTS = HERE / "requirements.txt"
IDENTITY = runpy.run_path(str(ROOT / "sima_cli" / "utils" / "windows_installer.py"))

# The same CPython release windows.bat installs.
PYTHON_VERSION = "3.14.10"
PYTHON_TAG = "cp314"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
)
PYTHON_EMBED_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
PYTHON_PTH = "python314._pth"
PYTHON_STDLIB_ZIP = "python314.zip"
# Environment markers of the bundled runtime, to evaluate the dependencies' requirements.
TARGET_MARKERS = {
    "implementation_name": "cpython",
    "implementation_version": PYTHON_VERSION,
    "os_name": "nt",
    "platform_machine": "AMD64",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_system": "Windows",
    "platform_version": "",
    "python_full_version": PYTHON_VERSION,
    "python_version": "3.14",
    "sys_platform": "win32",
}
# distlib's launcher replaces <launcher_dir> with the folder it runs from.
LAUNCHER_SHEBANG = b"#!<launcher_dir>\\..\\runtime\\python.exe\n"
# The install folder, %LOCALAPPDATA%\Programs\sima-cli\, adds about 60 characters.
MAX_BUNDLE_PATH = 180
# Every $(var.NAME) sima-cli.wxs uses, and BundleDir for the harvested files.
CANDLE_VARIABLES = ("BundleDir", "ProductCode", "ProductName", "ProductVersion", "Publisher", "UpgradeCode")


def log(message: str) -> None:
    print(message, flush=True)


def run(command: list, **kwargs) -> subprocess.CompletedProcess:
    argv = [str(part) for part in command]
    log("+ " + subprocess.list2cmdline(argv))
    return subprocess.run(argv, check=True, **kwargs)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def msi_version(version: str) -> str:
    """Return the MSI ProductVersion X.Y.Z for a sima-cli version.

    Only the release part counts, so a branch build such as 2.1.24+ref.sha builds 2.1.24.
    """
    from packaging.version import InvalidVersion, Version

    try:
        parsed = Version(version)
    except InvalidVersion:
        raise SystemExit(f"Not a package version: {version}")
    if parsed.epoch or len(parsed.release) != 3:
        raise SystemExit(f"{version} has no X.Y.Z release for the MSI ProductVersion")
    major, minor, patch = parsed.release
    if major > 255 or minor > 255 or patch > 65535:
        raise SystemExit(f"{version} exceeds the MSI ProductVersion limits 255.255.65535")
    return f"{major}.{minor}.{patch}"


def is_release(version: str) -> bool:
    """Only a final X.Y.Z version is published; branch builds and pre-releases are CI-only."""
    return re.fullmatch(r"\d+\.\d+\.\d+", version) is not None


def wheel_version(wheel: Path) -> str:
    from packaging.utils import parse_wheel_filename

    name, version, _build, _tags = parse_wheel_filename(wheel.name)
    if name != "sima-cli":
        raise SystemExit(f"{wheel.name} is not a sima-cli wheel")
    return str(version)


def fetch_runtime(downloads: Path) -> Path:
    """Stage 1. Download into a folder this run creates, so no earlier file can stand in."""
    downloads.mkdir(parents=True)
    archive = downloads / PYTHON_EMBED_URL.rsplit("/", 1)[1]
    log(f"Downloading {PYTHON_EMBED_URL}")
    with urllib.request.urlopen(PYTHON_EMBED_URL, timeout=120) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    actual = sha256(archive)
    if actual != PYTHON_EMBED_SHA256:
        raise SystemExit(f"{archive.name} has SHA-256 {actual}, not the pinned {PYTHON_EMBED_SHA256}")
    log(f"SHA-256 matches the pin: {actual}")
    return archive


def check_python_signature(python: Path) -> None:
    """On Windows, the interpreter must carry a valid Python Software Foundation signature."""
    if os.name != "nt":
        log("Authenticode check of python.exe skipped: it runs on Windows only; the SHA-256 pin holds.")
        return
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:SIMA_CLI_PYTHON; "
        "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.GetNameInfo('SimpleName', $false) "
        "-ne 'Python Software Foundation') { exit 1 }"
    )
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            env=dict(os.environ, SIMA_CLI_PYTHON=str(python)), check=False)
    if result.returncode != 0:
        raise SystemExit(f"{python} is not validly signed by the Python Software Foundation")
    log("python.exe is signed by the Python Software Foundation.")


def extract_runtime(archive: Path, runtime: Path) -> None:
    """Stage 2. The ._pth file keeps the runtime isolated; `import site` adds Lib\\site-packages."""
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(runtime)
    pth = runtime / PYTHON_PTH
    lines = pth.read_text(encoding="utf-8").splitlines()
    if lines.count("#import site") != 1:
        raise SystemExit(f"{PYTHON_PTH} does not have exactly one '#import site' line: {lines}")
    lines[lines.index("#import site")] = "import site"
    pth.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    if not (runtime / "LICENSE.txt").is_file():
        raise SystemExit("The embeddable CPython has no LICENSE.txt to ship")


def install_packages(site_packages: Path, wheel: Path) -> None:
    """Stage 3. Exact pins, wheels for Windows x64 and CPython 3.14 only, no resolver."""
    run([
        sys.executable, "-m", "pip", "install", "--isolated", "--disable-pip-version-check",
        "--no-input", "--index-url", "https://pypi.org/simple",
        "--target", site_packages, "--platform", "win_amd64", "--python-version", "3.14",
        "--implementation", "cp", "--abi", PYTHON_TAG, "--only-binary=:all:", "--no-deps",
        "--no-compile", "-r", REQUIREMENTS, wheel,
    ])
    # Console scripts pip wrote for this build host; the bundle has its own launcher.
    scripts = site_packages / "bin"
    if scripts.is_dir():
        shutil.rmtree(scripts)


def distributions(site_packages: Path) -> dict:
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    found = {}
    for info in sorted(site_packages.glob("*.dist-info")):
        metadata = email.parser.Parser().parsestr((info / "METADATA").read_text(encoding="utf-8"))
        found[canonicalize_name(metadata["Name"])] = {
            "version": Version(metadata["Version"]),
            "requires": metadata.get_all("Requires-Dist") or [],
            "info": info,
        }
    return found


def check_requirements(site_packages: Path) -> list:
    """Stage 4. What pip check would say on the target: every needed requirement is met.

    Returns the bundled distributions that nothing requires.
    """
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    bundled = distributions(site_packages)
    problems, seen, pending = [], set(), [("sima-cli", "")]
    while pending:
        name, extra = pending.pop()
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        for text in bundled[name]["requires"]:
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate(dict(TARGET_MARKERS, extra=extra)):
                continue
            target = canonicalize_name(requirement.name)
            if target not in bundled:
                problems.append(f"{name} requires {requirement}, which is not bundled")
                continue
            if not requirement.specifier.contains(bundled[target]["version"], prereleases=True):
                problems.append(f"{name} requires {requirement}, but {target} {bundled[target]['version']} is bundled")
            pending.extend([(target, "")] + [(target, wanted) for wanted in sorted(requirement.extras)])
    if problems:
        raise SystemExit("The pinned requirements do not fit together for Windows x64:\n  " + "\n  ".join(problems))
    required = {name for name, _extra in seen}
    log(f"All requirements of {len(required)} distributions are met for Windows x64 / CPython 3.14.")
    return sorted(set(bundled) - required)


def sima_cli_dist_info(site_packages: Path) -> Path:
    found = list(site_packages.glob("sima_cli-*.dist-info"))
    if len(found) != 1:
        raise SystemExit(f"Expected one sima_cli .dist-info, found {[path.name for path in found]}")
    return found[0]


def write_installer_marker(site_packages: Path) -> None:
    """Stage 5. PEP 627: INSTALLER names the tool that manages this install."""
    (sima_cli_dist_info(site_packages) / "INSTALLER").write_text(
        IDENTITY["INSTALLER_MARKER"] + "\n", encoding="utf-8")


def compile_bundle(site_packages: Path, stdlib_zip: Path) -> list:
    """Stage 6. Bytecode for the bundled CPython; returns the sources that do not compile."""
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 14):
        raise SystemExit("Run build_msi.py with CPython 3.14: it writes the bundle's 3.14 bytecode")
    with zipfile.ZipFile(stdlib_zip) as zf:
        bundled_magic = zf.read(next(name for name in zf.namelist() if name.endswith(".pyc")))[:4]
    if bundled_magic != importlib.util.MAGIC_NUMBER:
        raise SystemExit("This CPython writes bytecode the bundled CPython does not read")
    failed = []
    with warnings.catch_warnings():
        # Third-party sources warn about escapes and such; that is theirs to fix, not a failure.
        warnings.simplefilter("ignore", SyntaxWarning)
        for source in sorted(site_packages.rglob("*.py")):
            try:
                py_compile.compile(str(source), doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
            except py_compile.PyCompileError:
                failed.append(source.relative_to(site_packages).as_posix())
    return failed


def write_launcher(bundle: Path, site_packages: Path) -> Path:
    """Stage 7. What distlib's ScriptMaker writes on Windows: launcher, shebang, zipped script.

    ScriptMaker writes .exe launchers only when it runs on Windows, so the same three parts are
    put together here from distlib's own launcher and script template.
    """
    import importlib.resources

    from distlib.scripts import SCRIPT_TEMPLATE

    entry_points = configparser.ConfigParser(delimiters=("=",))
    entry_points.read(sima_cli_dist_info(site_packages) / "entry_points.txt", encoding="utf-8")
    module, _, function = entry_points["console_scripts"]["sima-cli"].strip().partition(":")
    script = SCRIPT_TEMPLATE % {"module": module, "import_name": function.split(".")[0], "func": function}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        # A fixed timestamp keeps the launcher identical between builds of the same wheel.
        zf.writestr(zipfile.ZipInfo("__main__.py", date_time=(1980, 1, 1, 0, 0, 0)), script.encode("utf-8"))
    launcher = bundle / "bin" / "sima-cli.exe"
    launcher.parent.mkdir()
    launcher.write_bytes(
        importlib.resources.files("distlib").joinpath("t64.exe").read_bytes()
        + LAUNCHER_SHEBANG + archive.getvalue())
    return launcher


def check_path_lengths(bundle: Path) -> int:
    """Stage 8. Keep installed paths well below MAX_PATH."""
    longest = max((str(PureWindowsPath(*path.relative_to(bundle).parts)) for path in bundle.rglob("*")), key=len)
    if len(longest) > MAX_BUNDLE_PATH:
        raise SystemExit(f"Bundle path longer than {MAX_BUNDLE_PATH} characters: {longest}")
    return len(longest)


def licence_gaps(site_packages: Path) -> list:
    """Distributions whose .dist-info carries no licence or notice file."""
    pattern = re.compile(r"(licen[cs]e|copying|notice|authors)", re.IGNORECASE)
    return [
        info.name for info in sorted(site_packages.glob("*.dist-info"))
        if not any(pattern.search(path.name) for path in info.rglob("*") if path.is_file())
    ]


def stage(wheel: Path, expected_version: str | None, work: Path) -> dict:
    version = wheel_version(wheel)
    if expected_version and version != expected_version:
        raise SystemExit(f"{wheel.name} is version {version}, expected {expected_version}")
    msi_version(version)
    bundle = work / "bundle"
    runtime = bundle / "runtime"
    site_packages = runtime / "Lib" / "site-packages"

    log("[1/8] fetch")
    archive = fetch_runtime(work / "download")
    log("[2/8] runtime")
    extract_runtime(archive, runtime)
    check_python_signature(runtime / "python.exe")
    log("[3/8] packages")
    install_packages(site_packages, wheel)
    log("[4/8] check")
    unrequired = check_requirements(site_packages)
    if unrequired:
        log("Bundled although no declared requirement names them: " + ", ".join(unrequired))
    log("[5/8] marker")
    write_installer_marker(site_packages)
    log("[6/8] compile")
    not_compiled = compile_bundle(site_packages, runtime / PYTHON_STDLIB_ZIP)
    if not_compiled:
        log("Not valid Python 3.14, so never imported, left without bytecode: " + ", ".join(not_compiled))
    log("[7/8] launcher")
    launcher = write_launcher(bundle, site_packages)
    log("[8/8] paths")
    longest = check_path_lengths(bundle)

    files = [path for path in bundle.rglob("*") if path.is_file()]
    facts = {
        "version": version,
        "python": PYTHON_VERSION,
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "longest_path": longest,
        "launcher_shebang": LAUNCHER_SHEBANG.decode("ascii").strip(),
        "installer_marker": (sima_cli_dist_info(site_packages) / "INSTALLER").read_text(encoding="utf-8").strip(),
        "unrequired": unrequired,
        "not_compiled": not_compiled,
        "without_licence_file": licence_gaps(site_packages),
    }
    (work / "bundle.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    log(f"Staged {facts['files']} files, {facts['bytes']} bytes, in {bundle}; launcher {launcher.name}.")
    return facts


def smoke_test(bundle: Path, version: str) -> None:
    """Stage 9. The launcher finds the runtime next to it and sima-cli imports."""
    result = run([bundle / "bin" / "sima-cli.exe", "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 env=dict(os.environ, SIMA_CLI_CHECK_FOR_UPDATE="0"), encoding="utf-8", errors="replace")
    log(result.stdout)
    if not result.stdout.strip().endswith(f"SiMa CLI version: {version}"):
        raise SystemExit(f"bin\\sima-cli.exe did not report version {version}")


def wix_tool(name: str) -> str:
    wix = os.environ.get("WIX")
    candidate = Path(wix) / "bin" / f"{name}.exe" if wix else None
    if candidate and candidate.is_file():
        return str(candidate)
    found = shutil.which(f"{name}.exe")
    if not found:
        raise SystemExit(f"WiX 3 {name}.exe not found: set WIX to the WiX Toolset v3 folder")
    return found


def build_msi(work: Path, output: Path, version: str, product_version: str) -> dict:
    """Stages 10 and 11."""
    bundle = work / "bundle"
    build = work / f"msi-{product_version}"
    build.mkdir()
    product_code = "{" + str(uuid.uuid4()).upper() + "}"
    variables = {
        "BundleDir": str(bundle),
        "ProductCode": product_code,
        "ProductName": IDENTITY["PRODUCT_NAME"],
        "ProductVersion": product_version,
        "Publisher": IDENTITY["PUBLISHER"],
        "UpgradeCode": IDENTITY["UPGRADE_CODE"],
    }
    assert tuple(sorted(variables)) == CANDLE_VARIABLES
    harvested = build / "bundle-files.wxs"
    run([wix_tool("heat"), "dir", bundle, "-nologo", "-cg", "Bundle", "-dr", "INSTALLFOLDER", "-ag", "-srd",
         "-sfrag", "-sreg", "-scom", "-var", "var.BundleDir", "-out", harvested])
    objects = build / "obj"
    run([wix_tool("candle"), "-nologo", "-arch", "x64",
         *[f"-d{name}={value}" for name, value in variables.items()],
         "-out", str(objects) + os.sep, WXS, harvested])
    output.mkdir(parents=True, exist_ok=True)
    msi = output / f"sima-cli-{product_version}-x64.msi"
    run([wix_tool("light"), "-nologo", "-spdb", "-out", msi, *sorted(objects.glob("*.wixobj"))])
    digest = sha256(msi)
    (output / f"{msi.name}.sha256").write_text(f"{digest}  {msi.name}\n", encoding="utf-8")
    record = {
        "version": version,
        "msi_version": product_version,
        "release": is_release(version),
        "file": msi.name,
        "sha256": digest,
        "product_code": product_code,
        "upgrade_code": IDENTITY["UPGRADE_CODE"],
        "python": PYTHON_VERSION,
    }
    (output / "installer.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    log(f"Built {msi} ({msi.stat().st_size} bytes), SHA-256 {digest}, ProductCode {product_code}")
    return record


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wheel", type=Path, help="The built sima-cli wheel")
    parser.add_argument("--expected-version", help="Fail unless the wheel has this version")
    parser.add_argument("--output-directory", type=Path, required=True,
                        help="Where the MSI, its .sha256 and installer.json are written")
    parser.add_argument("--work-directory", type=Path,
                        help="New folder for the download and the bundle (default: <output>-work)")
    parser.add_argument("--msi-version", help="ProductVersion to use instead of the wheel's X.Y.Z")
    parser.add_argument("--stage-only", action="store_true", help="Stop after stage 8; runs on any system")
    parser.add_argument("--reuse-bundle", action="store_true",
                        help="Link another MSI from the bundle already staged in --work-directory")
    args = parser.parse_args(argv)
    output = args.output_directory.resolve()
    work = (args.work_directory or output.with_name(output.name + "-work")).resolve()

    if args.reuse_bundle:
        if args.wheel or args.stage_only:
            parser.error("--reuse-bundle takes neither --wheel nor --stage-only")
        facts = json.loads((work / "bundle.json").read_text(encoding="utf-8"))
        if args.expected_version and facts["version"] != args.expected_version:
            raise SystemExit(f"The staged bundle is version {facts['version']}, expected {args.expected_version}")
    else:
        if not args.wheel:
            parser.error("--wheel is required unless --reuse-bundle is given")
        work.mkdir(parents=True, exist_ok=False)
        facts = stage(args.wheel.resolve(), args.expected_version, work)
    if args.stage_only:
        return 0

    if os.name != "nt":
        raise SystemExit("Stages 9-11 (smoke test, MSI, record) need Windows and WiX 3; "
                         "the bundle is staged. Use --stage-only on other systems.")
    product_version = msi_version(args.msi_version or facts["version"])
    if not args.reuse_bundle:
        log("[9/11] smoke")
        smoke_test(work / "bundle", facts["version"])
    log("[10/11] msi, [11/11] record")
    build_msi(work, output, facts["version"], product_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
