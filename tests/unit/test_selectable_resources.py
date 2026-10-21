import io
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sima_cli.install.metadata_installer import (
    _download_and_validate_metadata,
    selectable_resource_handler,
)


@pytest.fixture
def metadata():
    return {
        "resources": ["install.sh", "required.tar.gz"],
        "resources-checksum": {"required.tar.gz": "a" * 64},
        "selectable-resources": [
            {
                "name": "SiMa Neat extras (tutorials/tests)",
                "resource": "extras.tar.gz",
                "selected": True,
                "checksum": "b" * 64,
            },
            {"name": "Required payload", "resource": "required.tar.gz"},
        ],
    }


@pytest.mark.parametrize("stdin", [io.StringIO(""), None])
def test_non_tty_skips_extras_and_preserves_required_resources(metadata, stdin, capsys):
    original = deepcopy(metadata)
    with patch("sima_cli.install.metadata_installer.sys.stdin", stdin), patch(
        "InquirerPy.inquirer.checkbox"
    ) as picker:
        result = selectable_resource_handler(metadata)

    picker.assert_not_called()
    assert result["selectable-resources"] == []
    assert result["resources"] == original["resources"]
    assert result["resources-checksum"] == original["resources-checksum"]
    assert "stdin is not a terminal" in capsys.readouterr().out


def test_non_tty_metadata_loading_continues_without_picker(metadata, tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with patch("sima_cli.install.metadata_installer.sys.stdin", io.StringIO("")), patch(
        "sima_cli.install.metadata_installer.validate_metadata"
    ), patch("InquirerPy.inquirer.checkbox") as picker:
        result, directory = _download_and_validate_metadata(
            str(path), check_compatibility=False
        )

    picker.assert_not_called()
    assert result["resources"] == metadata["resources"]
    assert result["selectable-resources"] == []
    assert directory == str(tmp_path)


def test_interactive_picker_keeps_preselection_and_adds_selected_extras(metadata):
    extra = metadata["selectable-resources"][0]
    with patch("sima_cli.install.metadata_installer.sys.stdin.isatty", return_value=True), patch(
        "InquirerPy.inquirer.checkbox"
    ) as picker:
        picker.return_value.execute.return_value = [extra["name"]]
        result = selectable_resource_handler(metadata)

    picker.return_value.execute.assert_called_once_with()
    choices = picker.call_args.kwargs["choices"]
    assert [choice["enabled"] for choice in choices] == [True, True, False]
    assert choices[-1]["value"] == "__skip__"
    assert result["resources"] == ["install.sh", "required.tar.gz", "extras.tar.gz"]
    assert result["resources-checksum"]["extras.tar.gz"] == "b" * 64
    assert result["selectable-resources"] == [extra]


def test_interactive_skip_preserves_required_resources(metadata):
    original = deepcopy(metadata)
    with patch("sima_cli.install.metadata_installer.sys.stdin.isatty", return_value=True), patch(
        "InquirerPy.inquirer.checkbox"
    ) as picker:
        picker.return_value.execute.return_value = ["__skip__"]
        result = selectable_resource_handler(metadata)

    picker.return_value.execute.assert_called_once_with()
    assert result["selectable-resources"] == []
    assert result["resources"] == original["resources"]
    assert result["resources-checksum"] == original["resources-checksum"]


def test_no_optional_resources_needs_no_picker(capsys):
    metadata = {"resources": ["install.sh"]}
    with patch("InquirerPy.inquirer.checkbox") as picker:
        assert selectable_resource_handler(metadata) == {"resources": ["install.sh"]}
    picker.assert_not_called()
    assert capsys.readouterr().out == ""
