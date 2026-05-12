import importlib.util
from pathlib import Path
from unittest.mock import patch


INSTALLER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "install" / "install.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sima_cli_install_stub", INSTALLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_branch_key_normalizes_slashes_and_spaces():
    installer = load_installer()

    assert installer.branch_key("feature/foo bar") == "feature-foo-bar"


def test_normalize_index_uses_tags_and_releases_fallback():
    installer = load_installer()

    assert installer.normalize_index(
        {"branches": ["main", "main", ""], "tags": ["v2.1.6", "v2.1.5"]}
    ) == (["main"], ["v2.1.5", "v2.1.6"])
    assert installer.normalize_index(
        {"branches": ["main"], "releases": ["v2.1.5"]}
    ) == (["main"], ["v2.1.5"])


def test_choose_ref_noninteractive_prefers_main():
    installer = load_installer()

    assert installer.choose_ref(["feature/foo", "main"], ["v2.1.5"], True) == "main"


def test_choose_ref_auto_selects_single_branch():
    installer = load_installer()

    assert installer.choose_ref(["main"], [], False) == "main"


def test_choose_ref_auto_selects_single_release():
    installer = load_installer()

    assert installer.choose_ref([], ["v2.1.5"], False) == "v2.1.5"


def test_resolve_ref_fetches_branches_when_not_provided():
    installer = load_installer()

    with patch.object(
        installer,
        "fetch_json",
        return_value={"branches": ["feature/foo", "main"], "tags": ["v2.1.5"]},
    ):
        assert installer.resolve_ref("https://example.invalid/sima-cli", None, True) == "main"


def test_resolve_tag_reads_latest_tag():
    installer = load_installer()

    with patch.object(installer, "fetch_text", return_value="abc1234\n") as fetch_text:
        assert installer.resolve_tag("https://example.invalid/sima-cli", "feature/foo", "latest") == "abc1234"

    fetch_text.assert_called_once_with("https://example.invalid/sima-cli/feature-foo/latest.tag")


def test_find_artifact_selects_package_zip():
    installer = load_installer()
    metadata = {
        "artifacts": [
            {"filename": "sima_cli-2.1.5-py3-none-any.whl"},
            {"filename": "sima-cli-package-2.1.5.zip"},
        ]
    }

    assert installer.find_artifact(metadata, ".zip", "sima-cli-package")["filename"] == "sima-cli-package-2.1.5.zip"


def test_artifact_url_uses_explicit_url_or_base_filename():
    installer = load_installer()

    assert installer.artifact_url("https://example.invalid/sima-cli", {"url": "https://cdn/pkg.zip"}) == "https://cdn/pkg.zip"
    assert (
        installer.artifact_url("https://example.invalid/sima-cli", {"filename": "pkg.zip"})
        == "https://example.invalid/sima-cli/pkg.zip"
    )
