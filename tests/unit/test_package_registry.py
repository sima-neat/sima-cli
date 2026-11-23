import json

import pytest
from click.testing import CliRunner

from sima_cli.install.registry import PackageRegistry, packages


def test_remove_entry_removes_only_the_exact_receipt(tmp_path):
    registry_path = tmp_path / "registry.json"
    installed_file = tmp_path / "installed-by-owner.txt"
    installed_file.write_text("owned by another installer", encoding="utf-8")
    registry = PackageRegistry(registry_path)
    registry.create_entry("example/package", "1.0.0", {}, tmp_path)
    registry.create_entry("example/package", "2.0.0", {}, tmp_path)

    registry.remove_entry("example/package", "1.0.0")

    receipts = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [(entry["package_name"], entry["version"]) for entry in receipts] == [
        ("example/package", "2.0.0")
    ]
    assert installed_file.read_text(encoding="utf-8") == "owned by another installer"


def test_create_entry_keeps_installer_fields_of_a_retried_receipt(tmp_path):
    registry = PackageRegistry(tmp_path / "registry.json")
    registry.create_entry("example/package", "1.0.0", {}, tmp_path)
    registry.update_fields("example/package", "1.0.0", {"native_packages": [{"owner": "core"}]})
    registry.update_state("example/package", "1.0.0", "installed")

    registry.create_entry("example/package", "1.0.0", {"retry": True}, tmp_path)

    receipt = PackageRegistry(tmp_path / "registry.json")._find("example/package", "1.0.0")
    assert receipt["native_packages"] == [{"owner": "core"}]
    assert receipt["state"] == "installing"
    assert receipt["metadata"] == {"retry": True}


def test_remove_entry_rejects_an_unknown_receipt(tmp_path):
    registry = PackageRegistry(tmp_path / "registry.json")

    with pytest.raises(ValueError, match="not found in registry"):
        registry.remove_entry("example/package", "1.0.0")


def test_packages_remove_reports_receipt_only_boundary(monkeypatch, tmp_path):
    registry_path = tmp_path / "registry.json"
    registry = PackageRegistry(registry_path)
    registry.create_entry("example/package", "1.0.0", {}, tmp_path)
    monkeypatch.setattr(
        "sima_cli.install.registry.PackageRegistry",
        lambda: PackageRegistry(registry_path),
    )

    result = CliRunner().invoke(
        packages,
        ["remove", "example/package", "--version", "1.0.0", "--yes"],
    )

    assert result.exit_code == 0
    assert "Removed local registry receipt" in result.output
    assert json.loads(registry_path.read_text(encoding="utf-8")) == []


def test_packages_remove_can_be_cancelled(monkeypatch, tmp_path):
    registry_path = tmp_path / "registry.json"
    registry = PackageRegistry(registry_path)
    registry.create_entry("example/package", "1.0.0", {}, tmp_path)
    monkeypatch.setattr(
        "sima_cli.install.registry.PackageRegistry",
        lambda: PackageRegistry(registry_path),
    )

    result = CliRunner().invoke(
        packages,
        ["remove", "example/package", "--version", "1.0.0"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "removal cancelled" in result.output
    assert len(json.loads(registry_path.read_text(encoding="utf-8"))) == 1


def test_packages_remove_reports_an_unknown_receipt_before_prompting(monkeypatch, tmp_path):
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(
        "sima_cli.install.registry.PackageRegistry",
        lambda: PackageRegistry(registry_path),
    )

    result = CliRunner().invoke(packages, ["remove", "example/package", "--version", "1.0.0"])

    assert result.exit_code == 1
    assert "?" not in result.output
    assert "example/package@1.0.0 not found in registry" in result.output


def test_packages_remove_honours_the_global_yes_option(monkeypatch, tmp_path):
    registry_path = tmp_path / "registry.json"
    PackageRegistry(registry_path).create_entry("example/package", "1.0.0", {}, tmp_path)
    monkeypatch.setattr(
        "sima_cli.install.registry.PackageRegistry",
        lambda: PackageRegistry(registry_path),
    )

    result = CliRunner().invoke(
        packages, ["remove", "example/package", "--version", "1.0.0"], obj={"yes": True}
    )

    assert result.exit_code == 0
    assert "Removed local registry receipt" in result.output
    assert json.loads(registry_path.read_text(encoding="utf-8")) == []
