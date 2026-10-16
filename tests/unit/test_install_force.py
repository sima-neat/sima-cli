"""Force installation reaches the package script without changing the parent environment."""
import os
from unittest.mock import Mock, patch

import pytest

from sima_cli.install import metadata_installer as installer


@pytest.mark.parametrize(
    "force,inherited,expected",
    [(True, None, "1"), (True, "0", "1"), (False, None, None), (False, "1", "1")],
)
def test_install_script_force_environment(tmp_path, monkeypatch, force, inherited, expected):
    if inherited is None:
        monkeypatch.delenv("FORCE_INSTALL", raising=False)
    else:
        monkeypatch.setenv("FORCE_INSTALL", inherited)
    metadata = {
        "name": "test",
        "version": "1",
        "installation": {"script": "echo install"},
    }
    with patch.object(installer, "_download_and_validate_metadata", return_value=(metadata, None)), \
         patch.object(installer, "registry"), \
         patch.object(installer, "_check_whether_disk_is_big_enough", return_value=True), \
         patch.object(installer, "_is_platform_compatible", return_value=True), \
         patch.object(installer, "_download_assets", return_value=[str(tmp_path / "package.deb")]), \
         patch.object(installer, "_mark_install_script_executable"), \
         patch.object(installer, "_combine_multipart_files"), \
         patch.object(installer, "_extract_archives_in_folder"), \
         patch.object(installer.subprocess, "run", return_value=Mock(returncode=0)) as run:
        installer.install_from_metadata("metadata", False, str(tmp_path), force=force)

    run.assert_called_once()
    script_env = run.call_args.kwargs["env"]
    assert script_env.get("FORCE_INSTALL") == expected
    assert script_env["SIMA_INSTALL_CONTEXT"] == "1"
    assert os.environ.get("FORCE_INSTALL") == inherited
