"""Local SDK routing defaults must not override hosted proxy configuration."""
import pytest

from sima_cli.sdk.neat import _initialize_insight_url_routing


@pytest.mark.parametrize("mode", ["direct", "proxy"])
def test_explicit_routing_is_preserved(tmp_path, mode):
    path = tmp_path / "url-routing"
    path.write_text(mode + "\n")
    _initialize_insight_url_routing(tmp_path)
    assert path.read_text() == mode + "\n"


@pytest.mark.parametrize("url", ["https://127.0.0.1:9900/", "https://insight.example:9900/"])
def test_legacy_launch_configuration_keeps_proxy_default(tmp_path, url):
    path = tmp_path / "launch-url"
    path.write_text(url + "\n")
    _initialize_insight_url_routing(tmp_path)
    assert not (tmp_path / "url-routing").exists()
    assert path.read_text() == url + "\n"


def test_fresh_local_configuration_declares_direct_and_is_idempotent(tmp_path):
    _initialize_insight_url_routing(tmp_path)
    _initialize_insight_url_routing(tmp_path)
    assert (tmp_path / "url-routing").read_text() == "direct\n"
