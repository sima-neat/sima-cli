import pytest
import requests

from sima_cli.utils import network
from sima_cli.utils.network import (
    configure_session_for_url,
    is_internal_artifactory_url,
    rewrite_cacheable_url,
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

    assert session.trust_env is True
    assert session.merge_environment_settings(
        "https://artifacts.eng.sima.ai/file", {}, None, None, None
    )["proxies"] == {}


def test_public_session_does_not_apply_netrc_auth(monkeypatch):
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda _url: ("ambient", "secret")
    )
    session = configure_session_for_url(
        requests.Session(), "https://artifacts.neat.sima.ai/file"
    )

    prepared = session.prepare_request(
        requests.Request("GET", "https://artifacts.neat.sima.ai/file")
    )

    assert "Authorization" not in prepared.headers


def test_internal_artifactory_session_applies_netrc_auth(monkeypatch):
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda _url: ("artifactory", "secret")
    )
    session = configure_session_for_url(
        requests.Session(), "https://artifacts.eng.sima.ai/artifactory/file"
    )

    prepared = session.prepare_request(
        requests.Request(
            "GET", "https://artifacts.eng.sima.ai/artifactory/file"
        )
    )

    assert prepared.headers["Authorization"].startswith("Basic ")


def test_redirected_netrc_auth_is_scoped_to_internal_artifactory(monkeypatch):
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda _url: ("ambient", "secret")
    )
    session = configure_session_for_url(requests.Session(), "")
    response = requests.Response()
    response.request = requests.Request(
        "GET", "https://artifacts.eng.sima.ai/artifactory/file"
    ).prepare()

    public_request = requests.Request(
        "GET",
        "https://signed-object.example/file",
        headers={"Authorization": "Bearer internal-token"},
    ).prepare()
    session.rebuild_auth(public_request, response)
    assert "Authorization" not in public_request.headers

    response.request = requests.Request(
        "GET", "https://artifacts.neat.sima.ai/file"
    ).prepare()
    internal_request = requests.Request(
        "GET", "https://artifacts.eng.sima.ai/artifactory/file"
    ).prepare()
    session.rebuild_auth(internal_request, response)
    assert internal_request.headers["Authorization"].startswith("Basic ")


def test_redirect_preserves_explicit_artifactory_auth(monkeypatch):
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda _url: ("ambient", "secret")
    )
    session = configure_session_for_url(requests.Session(), "")
    response = requests.Response()
    response.request = requests.Request(
        "GET", "https://artifacts.eng.sima.ai/artifactory/start"
    ).prepare()
    redirected = requests.Request(
        "GET",
        "https://artifacts.eng.sima.ai/artifactory/final",
        headers={"Authorization": "Bearer explicit-token"},
    ).prepare()

    session.rebuild_auth(redirected, response)

    assert redirected.headers["Authorization"] == "Bearer explicit-token"


def test_redirects_recalculate_proxy_policy_for_destination(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    session = configure_session_for_url(
        requests.Session(), "https://artifacts.eng.sima.ai/file"
    )

    public_request = requests.Request(
        "GET", "https://signed-object.example/file"
    ).prepare()
    internal_request = requests.Request(
        "GET", "https://artifacts.eng.sima.ai/artifactory/file"
    ).prepare()

    assert session.rebuild_proxies(public_request, {})["https"] == (
        "http://proxy.example:3128"
    )
    assert session.rebuild_proxies(
        internal_request, {"https": "http://proxy.example:3128"}
    ) == {}


def test_cache_session_uses_dedicated_ca_bundle(monkeypatch, tmp_path):
    ca_bundle = tmp_path / "cache-ca.pem"
    ca_bundle.write_text("test CA", encoding="utf-8")
    monkeypatch.setattr(network, "CACHE_CA_BUNDLE", ca_bundle)
    monkeypatch.setenv("SIMA_CLI_CACHE_URL", "https://cache.example:8443")
    session = configure_session_for_url(requests.Session(), "")

    settings = session.merge_environment_settings(
        "https://cache.example:8443/models/model.tar.gz",
        {},
        None,
        None,
        None,
    )

    assert settings["verify"] == str(ca_bundle)


def test_artifact_cache_rewrites_only_approved_origin(monkeypatch):
    monkeypatch.setenv(
        "SIMA_CLI_CACHE_URL", "https://cache.example:8443"
    )

    assert rewrite_cacheable_url(
        "https://artifacts.neat.sima.ai/daily-platform-images/build/file.img.gz"
    ) == "https://cache.example:8443/daily-platform-images/build/file.img.gz"
    assert rewrite_cacheable_url(
        "https://artifacts.neat.sima.ai/sima-cli/latest.whl"
    ) == "https://cache.example:8443/sima-cli/latest.whl"
    assert rewrite_cacheable_url(
        "https://example.com/daily-platform-images/build/file.img.gz"
    ) == "https://example.com/daily-platform-images/build/file.img.gz"


def test_artifact_cache_rejects_credentials_and_paths(monkeypatch):
    monkeypatch.setenv(
        "SIMA_CLI_CACHE_URL", "http://user:secret@cache.example/mirror"
    )

    with pytest.raises(ValueError, match="SIMA_CLI_CACHE_URL"):
        rewrite_cacheable_url(
            "https://artifacts.neat.sima.ai/daily-platform-images/build/file.img.gz"
        )


def test_artifact_cache_rejects_plain_http(monkeypatch):
    monkeypatch.setenv("SIMA_CLI_CACHE_URL", "http://cache.example:8080")

    with pytest.raises(ValueError, match="must be an HTTPS origin"):
        rewrite_cacheable_url(
            "https://artifacts.neat.sima.ai/models/model.tar.gz"
        )
