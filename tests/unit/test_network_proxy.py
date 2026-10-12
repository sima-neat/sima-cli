import requests

from sima_cli.utils.network import (
    configure_session_for_url,
    is_internal_artifactory_url,
)


def test_only_configured_artifactory_origin_is_internal():
    assert is_internal_artifactory_url("https://artifacts.eng.sima.ai/artifactory/file")
    assert not is_internal_artifactory_url("https://artifacts.eng.sima.ai.evil.example/file")
    assert not is_internal_artifactory_url("https://artifacts.neat.sima.ai/file")
    assert not is_internal_artifactory_url("https://github.com/sima-neat/sima-cli")


def test_public_session_uses_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    session = configure_session_for_url(requests.Session(), "https://artifacts.neat.sima.ai/file")

    assert session.trust_env is True
    assert session.merge_environment_settings(
        "https://artifacts.neat.sima.ai/file", {}, None, None, None
    )["proxies"]["https"] == "http://proxy.example:3128"


def test_internal_artifactory_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    session = configure_session_for_url(requests.Session(), "https://artifacts.eng.sima.ai/file")

    assert session.trust_env is False
    assert session.merge_environment_settings(
        "https://artifacts.eng.sima.ai/file", {}, None, None, None
    )["proxies"] == {}
