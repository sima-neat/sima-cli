"""Bootimg defaults and explicit platform overrides reach the correct backend."""
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sima_cli.cli import bootimg_cmd, main


@pytest.mark.parametrize('options,board,firmware', [
    ([], 'modalix', 'elxr'),
    (['--boardtype', 'mlsoc', '--fwtype', 'yocto'], 'davinci', 'yocto'),
    (['--fwtype', 'yocto'], 'modalix', 'yocto'),
    (['--boardtype', 'mlsoc'], 'davinci', 'elxr'),
])
@pytest.mark.parametrize('netboot', [False, True])
def test_bootimg_target_defaults_and_overrides(options, board, firmware, netboot):
    with patch('sima_cli.update.bootimg.write_image') as write, \
            patch('sima_cli.update.netboot.setup_netboot') as setup:
        result = CliRunner().invoke(bootimg_cmd, ['-v', '3.0.0'] + options +
                                   (['--netboot'] if netboot else []), obj={})
    assert result.exit_code == 0, result.output
    if netboot:
        write.assert_not_called()
        setup.assert_called_once_with('3.0.0', board, False, False, flavor='headless',
                                      rootfs=None, swtype=firmware, allow_daily_fallback=False,
                                      devkit=None, auto_confirm_reboot=False)
    else:
        setup.assert_not_called()
        write.assert_called_once_with('3.0.0', board, firmware, False,
                                      flavor='headless', recovery=False)


@pytest.mark.parametrize("global_options,bootimg_options", [
    (["-y"], []),
    ([], ["-y"]),
    ([], ["--yes"]),
])
def test_netboot_accepts_reboot_confirmation_flag(global_options, bootimg_options):
    with patch("sima_cli.cli.check_for_update", return_value=False), \
            patch("sima_cli.update.netboot.setup_netboot") as setup:
        result = CliRunner().invoke(
            main, [*global_options, "bootimg", "-v", "3.0.0", "--netboot", *bootimg_options]
        )
    assert result.exit_code == 0, result.output
    assert setup.call_args.kwargs["auto_confirm_reboot"] is True


def test_help_shows_new_defaults():
    result = CliRunner().invoke(bootimg_cmd, ['--help'])
    assert result.exit_code == 0
    assert '[default: modalix]' in result.output
    assert '[default: elxr]' in result.output
