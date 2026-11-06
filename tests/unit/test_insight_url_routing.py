"""SDK creation defaults to direct routing; hosting owns later proxy setup."""
from pathlib import Path
from unittest.mock import patch

import pytest

from sima_cli.sdk.neat import prepare_neat_container_run


def prepare(workspace, **kwargs):
    with patch("sima_cli.sdk.neat._is_port_available", return_value=True), \
         patch("sima_cli.sdk.neat._ensure_certificates", return_value=(workspace / "cert.pem", workspace / "key.pem")), \
         patch("sima_cli.sdk.neat._detect_webrtc_host_ip", return_value="192.0.2.10"):
        return prepare_neat_container_run(str(workspace), "sdk-latest", **kwargs)


@pytest.mark.parametrize("mode", [None, "direct", "proxy"])
@pytest.mark.parametrize("launch", [None, "https://127.0.0.1:9900/", "https://insight.example:9900/"])
def test_setup_writes_direct_without_changing_launch_or_ice_settings(tmp_path, mode, launch):
    directory = tmp_path / ".sdk-latest" / "insight-config"
    directory.mkdir(parents=True)
    if mode is not None:
        (directory / "url-routing").write_text(mode + "\n")
    if launch is not None:
        (directory / "launch-url").write_text(launch + "\n")
    (directory / "browser-ice-address").write_text("192.0.2.10\n")
    prepare(tmp_path)
    assert (directory / "url-routing").read_text() == "direct\n"
    assert (directory / "browser-ice-address").read_text() == "192.0.2.10\n"
    if launch is None:
        assert not (directory / "launch-url").exists()
    else:
        assert (directory / "launch-url").read_text() == launch + "\n"


def test_hosted_provisioner_can_apply_proxy_after_setup(tmp_path):
    config = prepare(tmp_path)
    directory = Path(config.config_host_dir)
    assert (directory / "url-routing").read_text() == "direct\n"
    (directory / "launch-url").write_text("https://insight.example/\n")
    (directory / "url-routing").write_text("proxy\n")
    assert (directory / "url-routing").read_text() == "proxy\n"
    # Creating/recreating the SDK again resets the SDK default. Hosted
    # provisioning must reapply its override after that setup step too.
    prepare(tmp_path)
    assert (directory / "url-routing").read_text() == "direct\n"


@pytest.mark.parametrize("option", ["minimal", "no_insight"])
def test_setup_without_insight_leaves_existing_routing_untouched(tmp_path, option):
    directory = tmp_path / ".sdk-latest" / "insight-config"
    directory.mkdir(parents=True)
    (directory / "url-routing").write_text("proxy\n")
    prepare(tmp_path, **{option: True})
    assert (directory / "url-routing").read_text() == "proxy\n"
