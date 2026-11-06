"""Routing is an explicit SDK setup override, independent of DevKit transport."""
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sima_cli.sdk.commands import sdk
from sima_cli.sdk.install import setup_and_start
from sima_cli.sdk.neat import _write_insight_url_routing, prepare_neat_container_run


@pytest.mark.parametrize("mode", [None, "direct", "proxy"])
@pytest.mark.parametrize("launch", [None, "https://127.0.0.1:9900/", "https://insight.example:9900/"])
def test_default_setup_preserves_all_routing_contracts(tmp_path, mode, launch):
    if mode is not None:
        (tmp_path / "url-routing").write_text(mode + "\n")
    if launch is not None:
        (tmp_path / "launch-url").write_text(launch + "\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    _write_insight_url_routing(tmp_path, None)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("mode", ["direct", "proxy"])
def test_explicit_override_updates_only_the_routing_declaration(tmp_path, mode):
    (tmp_path / "url-routing").write_text("proxy\n" if mode == "direct" else "direct\n")
    (tmp_path / "launch-url").write_text("https://insight.example/\n")
    (tmp_path / "browser-ice-address").write_text("192.0.2.10\n")
    _write_insight_url_routing(tmp_path, mode)
    assert (tmp_path / "url-routing").read_text() == mode + "\n"
    assert (tmp_path / "launch-url").read_text() == "https://insight.example/\n"
    assert (tmp_path / "browser-ice-address").read_text() == "192.0.2.10\n"


@pytest.mark.parametrize("mode", ["direct", "proxy"])
def test_setup_option_forwards_explicit_routing(mode):
    with patch("sima_cli.sdk.commands.check_and_start_docker"), \
         patch("sima_cli.sdk.commands.setup_and_start") as setup:
        result = CliRunner().invoke(sdk, ["setup", "--insight-url-routing", mode, "-y", "-n"])
    assert result.exit_code == 0, result.output
    assert setup.call_args.kwargs["insight_url_routing"] == mode


def test_setup_has_no_implicit_routing_override():
    with patch("sima_cli.sdk.commands.check_and_start_docker"), \
         patch("sima_cli.sdk.commands.setup_and_start") as setup:
        result = CliRunner().invoke(sdk, ["setup", "-y", "-n"])
    assert result.exit_code == 0, result.output
    assert setup.call_args.kwargs["insight_url_routing"] is None


@pytest.mark.parametrize("excluded", ["minimal", "no_insight"])
def test_routing_override_requires_insight(excluded):
    with pytest.raises(RuntimeError, match="cannot be combined"):
        setup_and_start(insight_url_routing="direct", **{excluded: True})


def test_hosted_launch_url_can_be_written_after_default_sdk_setup(tmp_path):
    _write_insight_url_routing(tmp_path, None)
    (tmp_path / "launch-url").write_text("https://insight.example/\n")
    _write_insight_url_routing(tmp_path, None)
    assert not (tmp_path / "url-routing").exists()


@pytest.mark.parametrize("mode", [None, "direct", "proxy"])
def test_new_container_configuration_applies_only_an_explicit_override(tmp_path, mode):
    with patch("sima_cli.sdk.neat._is_port_available", return_value=True), \
         patch("sima_cli.sdk.neat._ensure_certificates", return_value=(tmp_path / "cert.pem", tmp_path / "key.pem")), \
         patch("sima_cli.sdk.neat._detect_webrtc_host_ip", return_value="192.0.2.10"):
        config = prepare_neat_container_run(str(tmp_path), "sdk-latest", insight_url_routing=mode)
    path = Path(config.config_host_dir) / "url-routing"
    if mode is None:
        assert not path.exists()
    else:
        assert path.read_text() == mode + "\n"
