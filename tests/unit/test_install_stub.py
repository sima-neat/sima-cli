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


def test_pypi_release_ref_parsing():
    installer = load_installer()

    assert installer.is_pypi_release_ref("v2.1.5")
    assert installer.version_from_release_ref("v2.1.5") == "2.1.5"
    assert not installer.is_pypi_release_ref("main")


def test_fetch_pypi_releases_reads_available_release_versions():
    installer = load_installer()

    with patch.object(
        installer,
        "fetch_json",
        return_value={
            "releases": {
                "2.1.4": [{"filename": "sima_cli-2.1.4-py3-none-any.whl"}],
                "2.1.5": [{"filename": "sima_cli-2.1.5-py3-none-any.whl"}],
                "2.1.6": [],
            }
        },
    ):
        assert installer.fetch_pypi_releases() == ["v2.1.4", "v2.1.5"]


def test_fetch_pypi_releases_can_limit_to_recent_versions():
    installer = load_installer()

    with patch.object(
        installer,
        "fetch_json",
        return_value={
            "releases": {
                "2.1.1": [{}],
                "2.1.2": [{}],
                "2.1.3": [{}],
                "2.1.4": [{}],
                "2.1.5": [{}],
                "2.1.6": [{}],
            }
        },
    ):
        assert installer.fetch_pypi_releases(limit=5) == [
            "v2.1.2",
            "v2.1.3",
            "v2.1.4",
            "v2.1.5",
            "v2.1.6",
        ]


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


def test_install_uses_pypi_for_release_ref():
    installer = load_installer()

    class Args:
        base_url = "https://example.invalid/sima-cli"
        ref = "v2.1.5"
        version = "latest"
        noninteractive = True
        current_env = False

    with patch.object(installer, "install_from_pypi") as install_from_pypi, \
         patch.object(installer, "resolve_metadata") as resolve_metadata:
        installer.install(Args())

    install_from_pypi.assert_called_once_with("v2.1.5")
    resolve_metadata.assert_not_called()


def test_install_artifact_current_env_installs_wheel_without_helper():
    installer = load_installer()

    class Args:
        base_url = "https://example.invalid/sima-cli"
        ref = "main"
        version = "abc1234"
        noninteractive = True
        current_env = True

    metadata = {
        "artifacts": [
            {"filename": "sima-cli-package-2.1.5.zip", "url": "https://cdn/pkg.zip"},
        ],
    }

    with patch.object(installer, "resolve_metadata", return_value=metadata), \
         patch.object(installer, "download_file"), \
         patch.object(installer, "extract_package", return_value=Path("/tmp/package")), \
         patch.object(installer, "find_one", return_value=Path("/tmp/package/sima_cli-2.1.5.whl")), \
         patch.object(installer, "install_wheel_current_env") as install_current, \
         patch.object(installer, "run_helper") as run_helper:
        installer.install(Args())

    install_current.assert_called_once_with(Path("/tmp/package/sima_cli-2.1.5.whl"))
    run_helper.assert_not_called()
