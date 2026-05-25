import io
import zipfile
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.upgrade import selfupdate as selfupdate_module


def test_selfupdate_dev_uses_vulcan_dev_environment():
    runner = CliRunner()

    with patch.object(selfupdate_module, "_update_from_vulcan") as update:
        result = runner.invoke(selfupdate_module.selfupdate, ["--dev", "--branch", "main"], obj={})

    assert result.exit_code == 0
    update.assert_called_once()
    assert update.call_args.args[1] == "dev"
    assert update.call_args.kwargs["branch"] == "main"


def test_selfupdate_staging_shortcut_uses_vulcan_staging_environment():
    runner = CliRunner()

    with patch.object(selfupdate_module, "_update_from_vulcan") as update:
        result = runner.invoke(selfupdate_module.selfupdate, ["--stg", "--branch", "main"], obj={})

    assert result.exit_code == 0
    assert update.call_args.args[1] == "staging"


def test_selfupdate_production_aliases_use_vulcan_production_environment():
    runner = CliRunner()

    for option in ("--prd", "--prod", "--neat", "--vulcan"):
        with patch.object(selfupdate_module, "_update_from_vulcan") as update:
            result = runner.invoke(selfupdate_module.selfupdate, [option, "--branch", "main"], obj={})

        assert result.exit_code == 0
        assert update.call_args.args[1] == "production"


def test_selfupdate_vulcan_mode_rejects_other_update_modes():
    runner = CliRunner()

    result = runner.invoke(selfupdate_module.selfupdate, ["--dev", "-v", "2.1.5"], obj={})

    assert result.exit_code == 1
    assert "Cannot use Vulcan self-update modes with -v or -m" in result.output


def test_selfupdate_branch_requires_vulcan_mode():
    runner = CliRunner()

    result = runner.invoke(selfupdate_module.selfupdate, ["--branch", "main"], obj={})

    assert result.exit_code == 1
    assert "--branch can only be used with --dev" in result.output


def test_update_from_vulcan_downloads_package_and_installs_wheel(tmp_path):
    package_bytes = _zip_bytes({"sima_cli-2.1.7-py3-none-any.whl": b"wheel"})
    base_url = selfupdate_module.SELFUPDATE_ENV_BASE_URLS["dev"]
    fake_client = _FakeClient({
        f"{base_url}/sima-cli/main/latest.tag": "abcdef123456\n",
        f"{base_url}/sima-cli/main/abcdef123456/metadata.json": {
            "resources": ["install_vulcan_package.py", "sima-cli-package-2.1.7+main.abcdef123456.zip"],
            "resources-checksum": {},
        },
        f"{base_url}/sima-cli/main/abcdef123456/sima-cli-package-2.1.7%2Bmain.abcdef123456.zip": package_bytes,
    })

    with patch.object(selfupdate_module, "_is_windows", return_value=False), \
         patch.object(selfupdate_module.tempfile, "mkdtemp", return_value=str(tmp_path)), \
         patch.object(selfupdate_module.subprocess, "run") as run:
        selfupdate_module._update_from_vulcan(
            "/usr/bin/python3",
            "dev",
            branch="main",
            client=fake_client,
        )

    wheel = tmp_path / "package" / "sima_cli-2.1.7-py3-none-any.whl"
    assert wheel.read_bytes() == b"wheel"
    run.assert_called_once_with(
        ["/usr/bin/python3", "-m", "pip", "install", "--force-reinstall", str(wheel)],
        check=True,
    )
    assert fake_client.urls == [
        "https://artifacts.neat.paconsultings.com/sima-cli/main/latest.tag",
        "https://artifacts.neat.paconsultings.com/sima-cli/main/abcdef123456/metadata.json",
        "https://artifacts.neat.paconsultings.com/sima-cli/main/abcdef123456/sima-cli-package-2.1.7%2Bmain.abcdef123456.zip",
    ]


def test_update_from_vulcan_menu_includes_recent_pypi_releases():
    base_url = selfupdate_module.SELFUPDATE_ENV_BASE_URLS["dev"]
    fake_client = _FakeClient({
        f"{base_url}/sima-cli/branches.json": {
            "branches": [
                {"name": "main", "key": "main"},
                {"name": "feature/selfupdate", "key": "feature-selfupdate"},
            ],
        },
        selfupdate_module.PUBLIC_PYPI_JSON_URL: {
            "releases": {
                "2.1.1": [{}],
                "2.1.2": [{}],
                "2.1.3": [{}],
                "2.1.4": [{}],
                "2.1.5": [{}],
                "2.1.6": [{}],
                "2.1.7": [{}],
            },
        },
    })

    with patch.object(selfupdate_module, "select_from_menu", return_value="v2.1.7") as select, \
         patch.object(selfupdate_module, "_update_from_pypi") as update:
        selfupdate_module._update_from_vulcan(
            "/usr/bin/python3",
            "dev",
            client=fake_client,
        )

    select.assert_called_once_with(
        "sima-cli branches or releases",
        ["feature/selfupdate", "main", "v2.1.3", "v2.1.4", "v2.1.5", "v2.1.6", "v2.1.7"],
    )
    update.assert_called_once_with("/usr/bin/python3", "2.1.7")


def test_update_from_vulcan_branch_accepts_release_ref():
    with patch.object(selfupdate_module, "_update_from_pypi") as update:
        selfupdate_module._update_from_vulcan(
            "/usr/bin/python3",
            "staging",
            branch="v2.1.8",
            client=_FakeClient({}),
        )

    update.assert_called_once_with("/usr/bin/python3", "2.1.8")


def _zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def read_bytes(self, url, headers=None):
        self.urls.append(url)
        value = self.responses[url]
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        raise TypeError(f"Response for {url} is not bytes or text.")

    def read_text(self, url, headers=None):
        self.urls.append(url)
        value = self.responses[url]
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8")
        raise TypeError(f"Response for {url} is not text.")

    def read_json(self, url, headers=None):
        self.urls.append(url)
        value = self.responses[url]
        if isinstance(value, dict):
            return value
        raise TypeError(f"Response for {url} is not JSON.")
