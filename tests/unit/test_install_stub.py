import importlib.util
import urllib.error
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


INSTALLER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "install" / "install.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sima_cli_install_stub", INSTALLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_branch_key_normalizes_slashes_and_spaces():
    installer = load_installer()

    assert installer.branch_key("feature/foo bar") == "feature%2Ffoo%20bar"


def test_join_url_double_encodes_branch_key_so_cdn_sees_published_key():
    installer = load_installer()

    assert installer.join_url(
        "https://example.invalid/sima-cli", installer.branch_key("feature/foo"), "latest.tag"
    ) == "https://example.invalid/sima-cli/feature%252Ffoo/latest.tag"


def test_join_url_leaves_slashless_refs_untouched():
    installer = load_installer()

    assert installer.join_url(
        "https://example.invalid/sima-cli/", installer.branch_key("main"), "latest.tag"
    ) == "https://example.invalid/sima-cli/main/latest.tag"
    assert installer.join_url(
        "https://example.invalid/sima-cli", installer.branch_key("v2.1.5"), "latest.tag"
    ) == "https://example.invalid/sima-cli/v2.1.5/latest.tag"


def test_normalize_index_uses_tags_and_releases_fallback():
    installer = load_installer()

    assert installer.normalize_index(
        {"branches": ["main", "main", ""], "tags": ["v2.1.6", "v2.1.5"]}
    ) == (["main"], ["v2.1.5", "v2.1.6"])
    assert installer.normalize_index(
        {"branches": ["main"], "releases": ["v2.1.5"]}
    ) == (["main"], ["v2.1.5"])


def test_normalize_index_accepts_vulcan_branch_objects():
    installer = load_installer()

    assert installer.normalize_index(
        {
            "branches": [
                {"name": "develop", "key": "develop"},
                {"name": "main", "key": "main"},
            ],
            "tags": [{"name": "v2.1.6"}],
        }
    ) == (["develop", "main"], ["v2.1.6"])


def test_normalize_index_decodes_key_only_branch_objects():
    installer = load_installer()

    assert installer.normalize_index(
        {"branches": [{"key": "feature%2Ffoo"}, {"key": "main"}]}
    ) == (["feature/foo", "main"], [])


def test_key_only_branch_object_resolves_to_the_published_key():
    installer = load_installer()
    payload = {"branches": [{"key": "feature%2Ffoo"}]}

    with patch.object(installer, "fetch_json", return_value=payload), \
         patch.object(installer, "fetch_pypi_releases", return_value=[]):
        ref = installer.resolve_ref("https://example.invalid/sima-cli", None, True)

    with patch.object(installer, "fetch_text", return_value="abc1234\n") as fetch_text:
        installer.resolve_tag("https://example.invalid/sima-cli", ref, "latest")

    fetch_text.assert_called_once_with("https://example.invalid/sima-cli/feature%252Ffoo/latest.tag")


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


def test_install_defaults_to_last_five_pypi_releases_in_menu():
    installer = load_installer()
    args = installer.build_parser().parse_args([])

    with patch.object(installer, "resolve_ref", return_value="v2.1.5") as resolve_ref, \
         patch.object(installer, "install_from_pypi") as install_from_pypi:
        installer.install(args)

    resolve_ref.assert_called_once_with(
        installer.DEFAULT_BASE_URL,
        None,
        False,
        pypi_release_limit=5,
    )
    install_from_pypi.assert_called_once_with("v2.1.5")


def test_resolve_tag_reads_latest_tag():
    installer = load_installer()

    with patch.object(installer, "fetch_text", return_value="abc1234\n") as fetch_text:
        assert installer.resolve_tag("https://example.invalid/sima-cli", "feature/foo", "latest") == "abc1234"

    fetch_text.assert_called_once_with("https://example.invalid/sima-cli/feature%252Ffoo/latest.tag")


def test_resolve_tag_reads_latest_tag_for_slashless_ref():
    installer = load_installer()

    with patch.object(installer, "fetch_text", return_value="abc1234\n") as fetch_text:
        assert installer.resolve_tag("https://example.invalid/sima-cli", "main", "latest") == "abc1234"

    fetch_text.assert_called_once_with("https://example.invalid/sima-cli/main/latest.tag")


def test_resolve_metadata_prefers_vulcan_metadata_json():
    installer = load_installer()

    with patch.object(installer, "fetch_json", return_value={"resources": []}) as fetch_json:
        metadata = installer.resolve_metadata("https://example.invalid/sima-cli", "feature/foo", "abc1234")

    fetch_json.assert_called_once_with("https://example.invalid/sima-cli/feature%252Ffoo/abc1234/metadata.json")
    assert metadata["_metadata_url"] == "https://example.invalid/sima-cli/feature%252Ffoo/abc1234/metadata.json"
    assert metadata["_resource_base_url"] == "https://example.invalid/sima-cli/feature%252Ffoo/abc1234"


def test_resolve_metadata_resource_urls_keep_double_encoded_branch_key():
    installer = load_installer()
    payload = {"resources": ["sima-cli-package-2.1.5+feature.abc1234.zip"]}

    with patch.object(installer, "fetch_json", return_value=payload):
        metadata = installer.resolve_metadata("https://example.invalid/sima-cli", "feature/foo", "abc1234")

    artifact = installer.find_artifact(metadata, ".zip", "sima-cli-package")

    assert artifact["url"] == (
        "https://example.invalid/sima-cli/feature%252Ffoo/abc1234/"
        "sima-cli-package-2.1.5%2Bfeature.abc1234.zip"
    )


def test_resolve_metadata_falls_back_to_legacy_json():
    installer = load_installer()
    missing = urllib.error.HTTPError(
        "https://example.invalid/sima-cli/main/abc1234/metadata.json",
        404,
        "Not Found",
        {},
        None,
    )

    with patch.object(installer, "fetch_json", side_effect=[missing, {"artifacts": []}]) as fetch_json:
        metadata = installer.resolve_metadata("https://example.invalid/sima-cli", "main", "abc1234")

    assert [call.args[0] for call in fetch_json.call_args_list] == [
        "https://example.invalid/sima-cli/main/abc1234/metadata.json",
        "https://example.invalid/sima-cli/main/abc1234.json",
    ]
    assert metadata["_metadata_url"] == "https://example.invalid/sima-cli/main/abc1234.json"


def test_find_artifact_selects_package_zip():
    installer = load_installer()
    metadata = {
        "artifacts": [
            {"filename": "sima_cli-2.1.5-py3-none-any.whl"},
            {"filename": "sima-cli-package-2.1.5.zip"},
        ]
    }

    assert installer.find_artifact(metadata, ".zip", "sima-cli-package")["filename"] == "sima-cli-package-2.1.5.zip"


def test_find_artifact_selects_package_zip_from_vulcan_resources():
    installer = load_installer()
    metadata = {
        "_resource_base_url": "https://example.invalid/sima-cli/main/abc1234",
        "resources": [
            "install_vulcan_package.py",
            "sima-cli-package-2.1.5+main.abc1234.zip",
        ],
    }

    artifact = installer.find_artifact(metadata, ".zip", "sima-cli-package")

    assert artifact["filename"] == "sima-cli-package-2.1.5+main.abc1234.zip"
    assert artifact["url"] == "https://example.invalid/sima-cli/main/abc1234/sima-cli-package-2.1.5%2Bmain.abc1234.zip"


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


class FakeUserEnvironment:
    """Stand in for winreg on HKCU\\Environment and the system Environment key, keeping values raw."""

    REG_SZ, REG_EXPAND_SZ = 1, 2
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    KEY_QUERY_VALUE, KEY_SET_VALUE = 1, 2
    SYSTEM_KEY = "SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment"

    def __init__(self, path=None, kind=REG_EXPAND_SZ, system_path=None):
        self.values = {} if path is None else {"Path": (path, kind)}
        self.system = {} if system_path is None else {"Path": (system_path, self.REG_EXPAND_SZ)}
        self.writes = []

    def _key(self, root, subkey):
        assert (root, subkey) in ((self.HKEY_CURRENT_USER, "Environment"), (self.HKEY_LOCAL_MACHINE, self.SYSTEM_KEY))
        key = MagicMock()
        key.__enter__.return_value = key
        key.values = self.values if root == self.HKEY_CURRENT_USER else self.system
        return key

    def CreateKeyEx(self, root, subkey, reserved, access):
        assert root == self.HKEY_CURRENT_USER
        return self._key(root, subkey)

    def OpenKey(self, root, subkey):
        return self._key(root, subkey)

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def SetValueEx(self, key, name, _reserved, kind, value):
        assert key.values is self.values, "only the user PATH may be written"
        self.values[name] = (value, kind)
        self.writes.append((name, value, kind))

    @staticmethod
    def ExpandEnvironmentStrings(value):
        return value.replace("%USERPROFILE%", "C:\\Users\\sima").replace("%SystemRoot%", "C:\\Windows")


def configure_windows_path(user_path, kind=FakeUserEnvironment.REG_EXPAND_SZ, system_path=None, files=()):
    installer = load_installer()
    registry = FakeUserEnvironment(user_path, kind, system_path)
    windll = SimpleNamespace(user32=MagicMock())
    with patch.dict("sys.modules", {"winreg": registry}), \
         patch.object(installer.platform, "system", return_value="Windows"), \
         patch("ctypes.windll", windll, create=True), \
         patch.object(installer.os.path, "isfile", side_effect=lambda path: path in files), \
         patch.object(installer.subprocess, "run") as run:
        installer._configure_pypi_shell_path(PureWindowsPath("C:\\Users\\sima\\.sima-cli-env"))
    run.assert_not_called()
    return registry, windll.user32.SendMessageTimeoutW


def test_windows_pypi_install_appends_to_the_raw_user_path():
    # setx PATH "%PATH%;..." replaced the user PATH with a literal %PATH% entry.
    registry, broadcast = configure_windows_path(
        "%USERPROFILE%\\AppData\\Local\\Microsoft\\WindowsApps;C:\\Tools;"
    )

    assert registry.writes == [(
        "Path",
        "%USERPROFILE%\\AppData\\Local\\Microsoft\\WindowsApps;C:\\Tools;"
        "C:\\Users\\sima\\.sima-cli-env\\Scripts",
        FakeUserEnvironment.REG_EXPAND_SZ,
    )]
    assert broadcast.call_args.args[1:4] == (0x1A, 0, "Environment")


def test_windows_pypi_install_keeps_the_path_value_kind_and_creates_a_missing_path():
    registry, _ = configure_windows_path("C:\\Tools", FakeUserEnvironment.REG_SZ)
    assert registry.writes[-1][2] == FakeUserEnvironment.REG_SZ
    registry, _ = configure_windows_path(None)
    assert registry.writes == [(
        "Path", "C:\\Users\\sima\\.sima-cli-env\\Scripts", FakeUserEnvironment.REG_EXPAND_SZ
    )]


def test_windows_pypi_install_does_not_repeat_an_existing_user_path_entry():
    registry, broadcast = configure_windows_path(
        "C:\\Tools;%USERPROFILE%\\.sima-cli-env\\Scripts\\"
    )
    assert registry.writes == []
    broadcast.assert_not_called()


OWN_CLI = "C:\\Users\\sima\\.sima-cli-env\\Scripts\\sima-cli.exe"


def test_windows_pypi_install_names_a_sima_cli_on_the_system_path_that_runs_first(capsys):
    # A new window searches the system PATH first, so another account's old elevated install
    # ran instead of the one just installed, and nothing said so.
    other = "C:\\Users\\other\\.sima-cli-env\\Scripts"
    registry, _ = configure_windows_path(
        "C:\\Tools", system_path=f"%SystemRoot%\\system32;{other}\\", files={other + "\\sima-cli.exe", OWN_CLI}
    )
    out = capsys.readouterr().out
    assert "WARNING: a new window will run another sima-cli, not the one just installed:\n" \
        f"    {other}\\sima-cli.exe\n" \
        "  It is on the system PATH, which Windows searches before your user PATH.\n" \
        "  It is the sima-cli of another account. Running windows.bat in that account from an " \
        "administrator window moves it to that account's own user PATH.\n" in out
    assert f'run it by its full path, in PowerShell:\n    & "{OWN_CLI}"\n' in out
    assert "Open a new PowerShell window" not in out
    # Only warned: the other account's entry is left alone.
    assert registry.system["Path"][0] == f"%SystemRoot%\\system32;{other}\\"
    assert [write[0] for write in registry.writes] == ["Path"]


def test_windows_pypi_install_names_a_sima_cli_earlier_on_the_user_path(capsys):
    pip_scripts = "C:\\Python312\\Scripts"
    configure_windows_path(
        f"{pip_scripts};%USERPROFILE%\\.sima-cli-env\\Scripts",
        system_path="%SystemRoot%\\system32",
        files={pip_scripts + "\\sima-cli.cmd", OWN_CLI},
    )
    out = capsys.readouterr().out
    assert f"    {pip_scripts}\\sima-cli.cmd\n" \
        "  It is on your user PATH, ahead of C:\\Users\\sima\\.sima-cli-env\\Scripts.\n" \
        f"  Uninstall that copy, or remove {pip_scripts} from the user PATH if nothing else needs it.\n" in out


def test_windows_pypi_install_does_not_warn_when_its_own_copy_runs_first(capsys):
    # This user's own folder left on the system PATH by an older installer still runs this copy.
    configure_windows_path(
        "%USERPROFILE%\\.sima-cli-env\\Scripts;C:\\Python312\\Scripts",
        system_path="c:\\users\\sima\\.sima-cli-env\\scripts",
        files={OWN_CLI, "c:\\users\\sima\\.sima-cli-env\\scripts\\sima-cli.exe", "C:\\Python312\\Scripts\\sima-cli.exe"},
    )
    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "Open a new PowerShell window to use sima-cli." in out
