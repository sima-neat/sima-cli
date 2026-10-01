import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import click
import pytest

from sima_cli.update.host import (
    HOST_PACKAGE_NAME,
    install_host_package,
    list_host_packages,
    resolve_host_package_url,
    select_host_package,
)


VERSION = "3.0.0_daily_develop_B1774"
EXPECTED_URL = (
    "https://artifacts.neat.sima.ai/daily-platform-images/"
    f"{VERSION}/artifacts/host_files/{HOST_PACKAGE_NAME}"
)
PACKAGE_BYTES = b"#!/bin/sh\n"
PACKAGE_SHA256 = hashlib.sha256(PACKAGE_BYTES).hexdigest()


def _index(*names):
    return {
        "schema_version": 1,
        "builds": [
            {
                "name": name,
                "build_number": int(name.rsplit("B", 1)[1]),
                "files": [
                    {
                        "path": f"artifacts/host_files/{HOST_PACKAGE_NAME}",
                        "size": len(PACKAGE_BYTES),
                        "sha256": PACKAGE_SHA256,
                    }
                ],
            }
            for name in names
        ],
    }


def test_resolve_host_package_url_from_daily_build_id():
    assert resolve_host_package_url(VERSION) == EXPECTED_URL


def test_resolve_host_package_url_accepts_exact_cdn_url():
    assert resolve_host_package_url(EXPECTED_URL) == EXPECTED_URL


@pytest.mark.parametrize(
    "value",
    [
        None,
        "../other-build",
        EXPECTED_URL.replace("artifacts.neat.sima.ai", "example.com"),
        EXPECTED_URL.replace(HOST_PACKAGE_NAME, "other.sh"),
        EXPECTED_URL + "?download=1",
    ],
)
def test_resolve_host_package_url_rejects_untrusted_source(value):
    with pytest.raises(click.UsageError):
        resolve_host_package_url(value)


@patch("sima_cli.update.host.requests.get")
def test_list_host_packages_uses_index_and_sorts_newest_first(get):
    get.return_value.json.return_value = _index(
        "3.0.0_daily_develop_B1768",
        VERSION,
    )

    packages = list_host_packages()

    assert [package["name"] for package in packages] == [
        VERSION,
        "3.0.0_daily_develop_B1768",
    ]


@patch("sima_cli.update.host.requests.get")
def test_list_host_packages_skips_builds_with_malformed_file_lists(get):
    index = _index(VERSION)
    index["builds"].insert(
        0,
        {
            "name": "3.0.0_daily_develop_B1775",
            "build_number": 1775,
            "files": None,
        },
    )
    get.return_value.json.return_value = index

    packages = list_host_packages()

    assert [package["name"] for package in packages] == [VERSION]


@patch("InquirerPy.inquirer.fuzzy")
@patch("sima_cli.update.host.list_host_packages")
def test_select_host_package_prompts_when_version_is_omitted(list_packages, fuzzy):
    packages = _index("3.0.0_daily_develop_B1768", VERSION)["builds"]
    for package in packages:
        host_file = package.pop("files")[0]
        package.update(size=host_file["size"], sha256=host_file["sha256"])
    list_packages.return_value = packages
    fuzzy.return_value.execute.return_value = VERSION

    selected = select_host_package(None)

    assert selected["name"] == VERSION
    assert fuzzy.call_args.kwargs["choices"][1]["value"] == VERSION


@patch("sima_cli.update.host.subprocess.run")
@patch("sima_cli.update.host.download_file_from_url")
@patch("sima_cli.update.host.select_host_package")
def test_install_host_package_downloads_from_cdn_and_runs_with_sudo(
    select_package,
    download_file,
    run,
    tmp_path,
):
    script = tmp_path / HOST_PACKAGE_NAME
    script.write_bytes(PACKAGE_BYTES)
    select_package.return_value = {
        "name": VERSION,
        "sha256": PACKAGE_SHA256,
    }
    download_file.return_value = str(script)
    run.return_value = subprocess.CompletedProcess([], 0)

    install_host_package(VERSION, auto_confirm=True)

    assert download_file.call_args.args[0] == EXPECTED_URL
    assert download_file.call_args.kwargs["internal"] is False
    assert run.call_args.args[0] == ["sudo", "sh", str(script)]
    assert run.call_args.kwargs["check"] is False
    assert Path(run.call_args.kwargs["cwd"]).name.startswith("sima-cli-host-update-")


@patch("sima_cli.update.host.subprocess.run")
@patch("sima_cli.update.host.download_file_from_url")
@patch("sima_cli.update.host.select_host_package")
def test_install_host_package_propagates_installer_failure(
    select_package, download_file, run, tmp_path
):
    script = tmp_path / HOST_PACKAGE_NAME
    script.write_bytes(PACKAGE_BYTES)
    select_package.return_value = {
        "name": VERSION,
        "sha256": PACKAGE_SHA256,
    }
    download_file.return_value = str(script)
    run.return_value = subprocess.CompletedProcess([], 9)

    with pytest.raises(click.ClickException, match="exited with code 9"):
        install_host_package(VERSION, auto_confirm=True)


@patch("sima_cli.update.host.subprocess.run")
@patch("sima_cli.update.host.download_file_from_url")
@patch("sima_cli.update.host.select_host_package")
def test_install_host_package_rejects_checksum_mismatch(
    select_package, download_file, run, tmp_path
):
    script = tmp_path / HOST_PACKAGE_NAME
    script.write_bytes(b"unexpected package")
    select_package.return_value = {
        "name": VERSION,
        "sha256": PACKAGE_SHA256,
    }
    download_file.return_value = str(script)

    with pytest.raises(click.ClickException, match="failed SHA-256 verification"):
        install_host_package(VERSION, auto_confirm=True)

    run.assert_not_called()
