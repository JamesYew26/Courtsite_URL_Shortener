from __future__ import annotations

import socket

import httpx
import pytest

from app.validator import URLValidationError, URLValidator


@pytest.mark.asyncio
async def test_rejects_invalid_syntax_and_schemes() -> None:
    validator = URLValidator()
    for bad_url in [
        "not-a-url",
        "ftp://files.example.com",
        "javascript:alert(1)",
        "http://",
        "http://[::1/",
        "https://example.com:99999/bad-port",
        "   ",
        "",
    ]:
        with pytest.raises(URLValidationError) as exc_info:
            await validator.validate(bad_url)
        assert exc_info.value.message


@pytest.mark.asyncio
async def test_rejects_ssrf_localhost_and_private_ips() -> None:
    validator = URLValidator()
    for internal_url in [
        "http://localhost:8000/shorten_url/",
        "http://sub.localhost/admin",
        "http://service.local/health",
        "http://metadata.internal/token",
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/router",
        "http://10.0.0.1/internal",
        "http://100.64.0.1/",
        "http://[::ffff:127.0.0.1]/admin",
    ]:
        with pytest.raises(URLValidationError, match="private|local|internal|restricted"):
            await validator.validate(internal_url)


@pytest.mark.asyncio
async def test_rejects_dns_resolving_to_private_ip_and_unresolvable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: object) -> list:
        if host == "rebind.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 0))]
        if host == "missing.example.invalid":
            raise socket.gaierror("Name or service not known")
        raise AssertionError(f"Unexpected host lookup: {host}")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    validator = URLValidator()

    with pytest.raises(URLValidationError, match="private|local|restricted"):
        await validator.validate("https://rebind.example.com/secret")

    with pytest.raises(URLValidationError, match="Could not resolve hostname"):
        await validator.validate("https://missing.example.invalid/page")


@pytest.mark.asyncio
async def test_head_success_and_get_fallback_on_405_and_403() -> None:
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.host == "head-ok.example.com":
            return httpx.Response(200)
        if request.url.host == "no-head.example.com":
            if request.method == "HEAD":
                return httpx.Response(405)
            return httpx.Response(200)
        if request.url.host == "forbidden-head.example.com":
            if request.method == "HEAD":
                return httpx.Response(403)
            return httpx.Response(200)
        if request.url.host == "dead.example.com":
            return httpx.Response(404)
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    validator = URLValidator(transport=transport, allow_private_ips=True)

    assert (
        await validator.validate("  https://head-ok.example.com/page  ")
        == "https://head-ok.example.com/page"
    )
    assert (
        await validator.validate("https://no-head.example.com/post")
        == "https://no-head.example.com/post"
    )
    assert (
        await validator.validate("https://forbidden-head.example.com/article")
        == "https://forbidden-head.example.com/article"
    )
    assert ("HEAD", "https://no-head.example.com/post") in calls
    assert ("GET", "https://no-head.example.com/post") in calls
    assert ("HEAD", "https://forbidden-head.example.com/article") in calls
    assert ("GET", "https://forbidden-head.example.com/article") in calls

    with pytest.raises(URLValidationError, match="404"):
        await validator.validate("https://dead.example.com/missing")

    with pytest.raises(URLValidationError, match="not accessible|Connection"):
        await validator.validate("https://unreachable.example.com")


@pytest.mark.asyncio
async def test_rejects_ssrf_via_redirect() -> None:
    requested_hosts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "93.184.216.34" and request.url.path == "/redirect-internal":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
        if request.url.host == "93.184.216.34" and request.url.path == "/redirect-public":
            return httpx.Response(302, headers={"Location": "https://93.184.216.34/final"})
        if request.url.host == "93.184.216.34" and request.url.path == "/final":
            return httpx.Response(200)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    validator = URLValidator(transport=transport, allow_private_ips=False)

    assert (
        await validator.validate("https://93.184.216.34/redirect-public")
        == "https://93.184.216.34/redirect-public"
    )

    with pytest.raises(URLValidationError, match="private|local|restricted"):
        await validator.validate("https://93.184.216.34/redirect-internal")

    assert "127.0.0.1" not in requested_hosts


@pytest.mark.asyncio
async def test_rejects_idna_failure_httpx_invalid_url_and_bad_redirect_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: object) -> list:
        host.encode("idna")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad-redirect":
            return httpx.Response(302, headers={"Location": "http://[::1/"})
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    validator = URLValidator(transport=transport, allow_private_ips=False)

    with pytest.raises(URLValidationError, match="Could not resolve hostname"):
        await validator.validate("https://..com/")

    with pytest.raises(URLValidationError, match="not accessible"):
        await validator.validate("https://example.com/foo\nbar")

    with pytest.raises(URLValidationError, match="not accessible"):
        await validator.validate("https://example.com/bad-redirect")
