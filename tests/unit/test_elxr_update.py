import unittest
from types import SimpleNamespace
from unittest.mock import call, mock_open, patch

from sima_cli.update.elxr import (
    ELXR_UPDATE_DOC_URL,
    EXTERNAL_REPO_URL,
    INTERNAL_REPO_URL,
    SIMAAI_OTA_FALLBACK,
    _resolve_simaai_ota,
    _get_installed_elxr_distro_version,
    _is_current_elxr_version,
    _select_elxr_repo_channel,
    _show_unsupported_specific_elxr_update,
    update_elxr,
)

EXTERNAL_BOOKWORM_REPO_LINE = f"deb {EXTERNAL_REPO_URL} bookworm non-free"
INTERNAL_BOOKWORM_REPO_LINE = f"deb {INTERNAL_REPO_URL} bookworm non-free"
EXTERNAL_TRIXIE_REPO_LINE = f"deb {EXTERNAL_REPO_URL} trixie non-free"
INTERNAL_TRIXIE_REPO_LINE = f"deb {INTERNAL_REPO_URL} trixie non-free"


class TestElxrRepoChannel(unittest.TestCase):
    def test_selects_internal_channel_and_comments_external(self):
        content = "\n".join([
            "deb http://deb.debian.org/debian bookworm main non-free-firmware",
            EXTERNAL_BOOKWORM_REPO_LINE,
            f"# {INTERNAL_BOOKWORM_REPO_LINE}",
            "",
        ])

        updated, changed, switching = _select_elxr_repo_channel(content, internal=True)

        self.assertTrue(changed)
        self.assertTrue(switching)
        self.assertIn(f"# {EXTERNAL_BOOKWORM_REPO_LINE}", updated)
        self.assertIn(INTERNAL_BOOKWORM_REPO_LINE, updated)

    def test_selects_external_channel_and_comments_internal(self):
        content = "\n".join([
            "deb http://deb.debian.org/debian bookworm main non-free-firmware",
            f"# {EXTERNAL_BOOKWORM_REPO_LINE}",
            INTERNAL_BOOKWORM_REPO_LINE,
            "",
        ])

        updated, changed, switching = _select_elxr_repo_channel(content, internal=False)

        self.assertTrue(changed)
        self.assertTrue(switching)
        self.assertIn(EXTERNAL_BOOKWORM_REPO_LINE, updated)
        self.assertIn(f"# {INTERNAL_BOOKWORM_REPO_LINE}", updated)

    def test_appends_missing_target_without_switch_warning(self):
        updated, changed, switching = _select_elxr_repo_channel(
            "deb http://deb.debian.org/debian bookworm main non-free-firmware\n",
            internal=False,
        )

        self.assertTrue(changed)
        self.assertFalse(switching)
        self.assertIn(EXTERNAL_BOOKWORM_REPO_LINE, updated)
        self.assertIn(f"# {INTERNAL_BOOKWORM_REPO_LINE}", updated)

    def test_appends_missing_inactive_channel(self):
        updated, changed, switching = _select_elxr_repo_channel(
            "\n".join([
                "deb http://deb.debian.org/debian bookworm main non-free-firmware",
                EXTERNAL_BOOKWORM_REPO_LINE,
                "",
            ]),
            internal=False,
        )

        self.assertTrue(changed)
        self.assertFalse(switching)
        self.assertIn(EXTERNAL_BOOKWORM_REPO_LINE, updated)
        self.assertIn(f"# {INTERNAL_BOOKWORM_REPO_LINE}", updated)

    def test_preserves_future_debian_suite_when_switching_channels(self):
        content = "\n".join([
            "deb http://deb.debian.org/debian trixie main non-free-firmware",
            EXTERNAL_TRIXIE_REPO_LINE,
            f"# {INTERNAL_TRIXIE_REPO_LINE}",
            "",
        ])

        updated, changed, switching = _select_elxr_repo_channel(content, internal=True)

        self.assertTrue(changed)
        self.assertTrue(switching)
        self.assertIn(f"# {EXTERNAL_TRIXIE_REPO_LINE}", updated)
        self.assertIn(INTERNAL_TRIXIE_REPO_LINE, updated)
        self.assertNotIn("bookworm non-free", updated)


class TestSimaaiOtaResolution(unittest.TestCase):
    @patch("sima_cli.update.elxr.shutil.which", return_value="/opt/bin/simaai-ota")
    def test_uses_path_command_when_available(self, _mock_which):
        self.assertEqual(_resolve_simaai_ota(), "/opt/bin/simaai-ota")

    @patch("sima_cli.update.elxr.os.access", return_value=True)
    @patch("sima_cli.update.elxr.os.path.isfile", return_value=True)
    @patch("sima_cli.update.elxr.shutil.which", return_value=None)
    def test_falls_back_to_usr_bin_when_not_on_path(self, _mock_which, _mock_isfile, _mock_access):
        self.assertEqual(_resolve_simaai_ota(), SIMAAI_OTA_FALLBACK)

    @patch("sima_cli.update.elxr.os.path.isfile", return_value=False)
    @patch("sima_cli.update.elxr.shutil.which", return_value=None)
    def test_returns_command_name_when_no_known_binary_found(self, _mock_which, _mock_isfile):
        self.assertEqual(_resolve_simaai_ota(), "simaai-ota")


class TestElxrVersionDetection(unittest.TestCase):
    @patch("builtins.open", new_callable=mock_open, read_data="DISTRO_VERSION = 2.0.0\n")
    @patch("sima_cli.update.elxr.BUILDINFO_FILES", ["/etc/buildinfo"])
    def test_reads_distro_version_from_buildinfo(self, _mock_open):
        self.assertEqual(_get_installed_elxr_distro_version(), "2.0.0")

    def test_current_version_matches_distro_version(self):
        self.assertTrue(_is_current_elxr_version("2.0.0", "2.0.0~git20251202-827", "2.0.0"))
        self.assertFalse(_is_current_elxr_version("2.1.0", "2.0.0~git20251202-827", "2.0.0"))


class TestUnsupportedElxrSpecificVersionUpdate(unittest.TestCase):
    @patch("sima_cli.update.elxr.Console")
    def test_warning_panel_mentions_unsupported_path_versions_and_doc(self, mock_console):
        _show_unsupported_specific_elxr_update(
            requested_version="2.0.0",
            current_version="2.1.0",
            latest_version="2.1.1",
        )

        panel = mock_console.return_value.print.call_args.args[0]
        body = panel.renderable.plain

        self.assertEqual(panel.border_style, "yellow")
        self.assertIn("specific version is not currently supported", body)
        self.assertIn("Current simaai-palette-modalix version: 2.1.0", body)
        self.assertIn("Requested simaai-palette-modalix version: 2.0.0", body)
        self.assertIn("Latest available simaai-palette-modalix version: 2.1.1", body)
        self.assertIn(ELXR_UPDATE_DOC_URL, body)

    @patch("sima_cli.update.elxr._show_unsupported_specific_elxr_update")
    @patch("sima_cli.update.elxr._get_installed_elxr_distro_version", return_value=None)
    @patch("sima_cli.update.elxr._get_installed_palette_version", return_value="2.1.0")
    @patch("sima_cli.update.elxr._get_available_palette_versions", return_value=["2.1.1", "2.1.0", "2.0.0"])
    @patch("sima_cli.update.elxr.subprocess.check_call")
    @patch("sima_cli.update.elxr.subprocess.call", return_value=0)
    @patch("sima_cli.update.elxr._ensure_elxr_repo_channel", return_value=True)
    @patch("sima_cli.update.elxr.print_current_versions")
    @patch("sima_cli.update.elxr.is_devkit_running_elxr", return_value=True)
    def test_explicit_specific_version_warns_without_running_ota(
        self,
        _mock_is_elxr,
        _mock_print_versions,
        _mock_ensure_channel,
        _mock_call,
        mock_check_call,
        _mock_available_versions,
        _mock_installed_version,
        _mock_distro_version,
        mock_warning,
    ):
        update_elxr("2.0.0", internal=False)

        mock_check_call.assert_called_once_with(["sudo", "apt", "update"])
        mock_warning.assert_called_once_with(
            requested_version="2.0.0",
            current_version="2.1.0",
            latest_version="2.1.1",
        )

    @patch("sima_cli.update.elxr._show_unsupported_specific_elxr_update")
    @patch("sima_cli.update.elxr._get_installed_elxr_distro_version", return_value=None)
    @patch("sima_cli.update.elxr._get_installed_palette_version", return_value="2.1.0")
    @patch("sima_cli.update.elxr._get_available_palette_versions", return_value=["2.1.1", "2.1.0", "2.0.0"])
    @patch("sima_cli.update.elxr.subprocess.check_call")
    @patch("sima_cli.update.elxr.subprocess.call", return_value=0)
    @patch("sima_cli.update.elxr._ensure_elxr_repo_channel", return_value=True)
    @patch("sima_cli.update.elxr.print_current_versions")
    @patch("sima_cli.update.elxr.is_devkit_running_elxr", return_value=True)
    def test_interactive_specific_version_selection_warns_without_running_ota(
        self,
        _mock_is_elxr,
        _mock_print_versions,
        _mock_ensure_channel,
        _mock_call,
        mock_check_call,
        _mock_available_versions,
        _mock_installed_version,
        _mock_distro_version,
        mock_warning,
    ):
        select_results = iter(["version", "cancel"])
        fake_inquirer = SimpleNamespace(
            select=lambda **_kwargs: SimpleNamespace(execute=lambda: next(select_results)),
            fuzzy=lambda **_kwargs: SimpleNamespace(execute=lambda: "2.0.0"),
        )

        with patch.dict("sys.modules", {"InquirerPy": SimpleNamespace(inquirer=fake_inquirer)}):
            update_elxr(None, internal=False)

        mock_check_call.assert_called_once_with(["sudo", "apt", "update"])
        mock_warning.assert_called_once_with(
            requested_version="2.0.0",
            current_version="2.1.0",
            latest_version="2.1.1",
        )

    @patch("sima_cli.update.elxr._show_unsupported_specific_elxr_update")
    @patch("sima_cli.update.elxr._get_installed_elxr_distro_version", return_value=None)
    @patch("sima_cli.update.elxr._get_installed_palette_version", return_value="2.1.0")
    @patch("sima_cli.update.elxr._get_available_palette_versions", return_value=["2.1.1", "2.1.0", "2.0.0"])
    @patch("sima_cli.update.elxr.subprocess.check_call")
    @patch("sima_cli.update.elxr.subprocess.call", return_value=0)
    @patch("sima_cli.update.elxr._ensure_elxr_repo_channel", return_value=True)
    @patch("sima_cli.update.elxr.print_current_versions")
    @patch("sima_cli.update.elxr.is_devkit_running_elxr", return_value=True)
    def test_interactive_unsupported_warning_can_go_back(
        self,
        _mock_is_elxr,
        _mock_print_versions,
        _mock_ensure_channel,
        _mock_call,
        mock_check_call,
        _mock_available_versions,
        _mock_installed_version,
        _mock_distro_version,
        mock_warning,
    ):
        select_results = iter(["version", "back", "cancel"])
        fake_inquirer = SimpleNamespace(
            select=lambda **_kwargs: SimpleNamespace(execute=lambda: next(select_results)),
            fuzzy=lambda **_kwargs: SimpleNamespace(execute=lambda: "2.0.0"),
        )

        with patch.dict("sys.modules", {"InquirerPy": SimpleNamespace(inquirer=fake_inquirer)}):
            update_elxr(None, internal=False)

        mock_check_call.assert_called_once_with(["sudo", "apt", "update"])
        mock_warning.assert_called_once_with(
            requested_version="2.0.0",
            current_version="2.1.0",
            latest_version="2.1.1",
        )

    @patch("sima_cli.update.elxr.click.confirm", return_value=True)
    @patch("sima_cli.update.elxr._show_unsupported_specific_elxr_update")
    @patch("sima_cli.update.elxr._resolve_simaai_ota", return_value="simaai-ota")
    @patch("sima_cli.update.elxr._get_installed_elxr_distro_version", return_value="2.1.0")
    @patch("sima_cli.update.elxr._get_installed_palette_version", return_value="2.1.0~git20251202-827")
    @patch("sima_cli.update.elxr._get_available_palette_versions", return_value=["2.1.1", "2.1.0", "2.0.0"])
    @patch("sima_cli.update.elxr.subprocess.check_call")
    @patch("sima_cli.update.elxr.subprocess.call", return_value=0)
    @patch("sima_cli.update.elxr._ensure_elxr_repo_channel", return_value=True)
    @patch("sima_cli.update.elxr.print_current_versions")
    @patch("sima_cli.update.elxr.is_devkit_running_elxr", return_value=True)
    def test_explicit_latest_version_runs_update(
        self,
        _mock_is_elxr,
        _mock_print_versions,
        _mock_ensure_channel,
        _mock_call,
        mock_check_call,
        _mock_available_versions,
        _mock_installed_version,
        _mock_distro_version,
        _mock_resolve_ota,
        mock_warning,
        _mock_confirm,
    ):
        update_elxr("2.1.1", internal=False)

        self.assertEqual(
            mock_check_call.call_args_list,
            [
                call(["sudo", "apt", "update"]),
                call(["sudo", "simaai-ota", "-f", "-o", "-v", "2.1.1"]),
            ],
        )
        mock_warning.assert_not_called()

    @patch("sima_cli.update.elxr.click.confirm", return_value=True)
    @patch("sima_cli.update.elxr._show_unsupported_specific_elxr_update")
    @patch("sima_cli.update.elxr._resolve_simaai_ota", return_value="simaai-ota")
    @patch("sima_cli.update.elxr._get_installed_elxr_distro_version", return_value="2.1.0")
    @patch("sima_cli.update.elxr._get_installed_palette_version", return_value="2.1.0~git20251202-827")
    @patch("sima_cli.update.elxr._get_available_palette_versions", return_value=["2.1.1", "2.1.0", "2.0.0"])
    @patch("sima_cli.update.elxr.subprocess.check_call")
    @patch("sima_cli.update.elxr.subprocess.call", return_value=0)
    @patch("sima_cli.update.elxr._ensure_elxr_repo_channel", return_value=True)
    @patch("sima_cli.update.elxr.print_current_versions")
    @patch("sima_cli.update.elxr.is_devkit_running_elxr", return_value=True)
    def test_interactive_latest_version_selection_runs_update(
        self,
        _mock_is_elxr,
        _mock_print_versions,
        _mock_ensure_channel,
        _mock_call,
        mock_check_call,
        _mock_available_versions,
        _mock_installed_version,
        _mock_distro_version,
        _mock_resolve_ota,
        mock_warning,
        _mock_confirm,
    ):
        fake_inquirer = SimpleNamespace(
            select=lambda **_kwargs: SimpleNamespace(execute=lambda: "version"),
            fuzzy=lambda **_kwargs: SimpleNamespace(execute=lambda: "2.1.1"),
        )

        with patch.dict("sys.modules", {"InquirerPy": SimpleNamespace(inquirer=fake_inquirer)}):
            update_elxr(None, internal=False)

        self.assertEqual(
            mock_check_call.call_args_list,
            [
                call(["sudo", "apt", "update"]),
                call(["sudo", "simaai-ota", "-f", "-o", "-v", "2.1.1"]),
            ],
        )
        mock_warning.assert_not_called()

    @patch("sima_cli.update.elxr.click.confirm", return_value=True)
    @patch("sima_cli.update.elxr._show_unsupported_specific_elxr_update")
    @patch("sima_cli.update.elxr._resolve_simaai_ota", return_value="simaai-ota")
    @patch("sima_cli.update.elxr._get_installed_elxr_distro_version", return_value="2.0.0")
    @patch("sima_cli.update.elxr._get_installed_palette_version", return_value="2.0.0~git20251202-827")
    @patch("sima_cli.update.elxr._get_available_palette_versions", return_value=["2.1.1", "2.1.0", "2.0.0"])
    @patch("sima_cli.update.elxr.subprocess.check_call")
    @patch("sima_cli.update.elxr.subprocess.call", return_value=0)
    @patch("sima_cli.update.elxr._ensure_elxr_repo_channel", return_value=True)
    @patch("sima_cli.update.elxr.print_current_versions")
    @patch("sima_cli.update.elxr.is_devkit_running_elxr", return_value=True)
    def test_explicit_same_version_runs_force_reinstall(
        self,
        _mock_is_elxr,
        _mock_print_versions,
        _mock_ensure_channel,
        _mock_call,
        mock_check_call,
        _mock_available_versions,
        _mock_installed_version,
        _mock_distro_version,
        _mock_resolve_ota,
        mock_warning,
        _mock_confirm,
    ):
        update_elxr("2.0.0", internal=False)

        self.assertEqual(
            mock_check_call.call_args_list,
            [
                call(["sudo", "apt", "update"]),
                call(["sudo", "simaai-ota", "-f", "-o", "-v", "2.0.0"]),
            ],
        )
        mock_warning.assert_not_called()

    @patch("sima_cli.update.elxr.click.confirm", return_value=True)
    @patch("sima_cli.update.elxr._show_unsupported_specific_elxr_update")
    @patch("sima_cli.update.elxr._resolve_simaai_ota", return_value="simaai-ota")
    @patch("sima_cli.update.elxr._get_installed_elxr_distro_version", return_value="2.0.0")
    @patch("sima_cli.update.elxr._get_installed_palette_version", return_value="2.0.0~git20251202-827")
    @patch("sima_cli.update.elxr._get_available_palette_versions", return_value=["2.1.1", "2.1.0", "2.0.0"])
    @patch("sima_cli.update.elxr.subprocess.check_call")
    @patch("sima_cli.update.elxr.subprocess.call", return_value=0)
    @patch("sima_cli.update.elxr._ensure_elxr_repo_channel", return_value=True)
    @patch("sima_cli.update.elxr.print_current_versions")
    @patch("sima_cli.update.elxr.is_devkit_running_elxr", return_value=True)
    def test_interactive_same_version_selection_runs_force_reinstall(
        self,
        _mock_is_elxr,
        _mock_print_versions,
        _mock_ensure_channel,
        _mock_call,
        mock_check_call,
        _mock_available_versions,
        _mock_installed_version,
        _mock_distro_version,
        _mock_resolve_ota,
        mock_warning,
        _mock_confirm,
    ):
        select_results = iter(["version", "confirm"])
        fake_inquirer = SimpleNamespace(
            select=lambda **_kwargs: SimpleNamespace(execute=lambda: next(select_results)),
            fuzzy=lambda **_kwargs: SimpleNamespace(execute=lambda: "2.0.0"),
        )

        with patch.dict("sys.modules", {"InquirerPy": SimpleNamespace(inquirer=fake_inquirer)}):
            update_elxr(None, internal=False)

        self.assertEqual(
            mock_check_call.call_args_list,
            [
                call(["sudo", "apt", "update"]),
                call(["sudo", "simaai-ota", "-f", "-o", "-v", "2.0.0"]),
            ],
        )
        mock_warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
