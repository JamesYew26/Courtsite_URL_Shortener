from __future__ import annotations

import asyncio
import ipaddress
import socket
import urllib.parse
from typing import Union

import httpx

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal")
_USER_AGENT = "Mozilla/5.0 (compatible; CourtsiteURLShortener/1.0; +https://courtsite.my)"


class URLValidationError(Exception):
    """Raised when a URL fails syntax, SSRF, or reachability validation."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _is_restricted_ip(ip_obj: IPAddress) -> bool:
    """Return True if an IP address belongs to a restricted or non-public range.

    SSRF (Server-Side Request Forgery) Defense:
    Because `POST /shorten_url/` makes a live server-side HTTP request to verify
    the user-supplied URL is accessible, an attacker could pass an internal IP
    (e.g., `http://127.0.0.1:8000/admin`, `http://192.168.1.1`, or AWS/GCP cloud
    metadata `http://169.254.169.254/latest/meta-data/`) to probe our private network.
    We block all loopback, private, link-local, multicast, reserved, unspecified,
    and non-global (such as RFC 6598 CGNAT `100.64.0.0/10`) IP ranges, including
    IPv4-mapped IPv6 addresses (`::ffff:127.0.0.1`).
    """
    if (
        ip_obj.is_loopback
        or ip_obj.is_private
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or not ip_obj.is_global
    ):
        return True
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        return _is_restricted_ip(ip_obj.ipv4_mapped)
    return False


class URLValidator:
    """Validates URL syntax, enforces SSRF protections, and checks HTTP reachability."""

    def __init__(
        self,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_private_ips: bool = False,
    ) -> None:
        self.timeout = timeout
        self.transport = transport
        self.allow_private_ips = allow_private_ips

    async def _validate_host_ssrf(self, hostname: str) -> None:
        normalized_host = hostname.lower().rstrip(".")
        if normalized_host == "localhost" or normalized_host.endswith(_LOCAL_HOST_SUFFIXES):
            raise URLValidationError("Access to local or internal hostnames is restricted")

        try:
            literal_ip = ipaddress.ip_address(normalized_host)
        except ValueError:
            literal_ip = None

        if literal_ip is not None:
            if _is_restricted_ip(literal_ip):
                raise URLValidationError(
                    "Access to private, local, or restricted network addresses is not allowed"
                )
            return

        try:
            addr_infos = await asyncio.to_thread(socket.getaddrinfo, normalized_host, None)
        except (socket.gaierror, UnicodeError) as exc:
            raise URLValidationError(f"Could not resolve hostname: {hostname}") from exc

        if not addr_infos:
            raise URLValidationError(f"Could not resolve hostname: {hostname}")

        for info in addr_infos:
            raw_ip = info[4][0].split("%")[0]
            try:
                resolved_ip = ipaddress.ip_address(raw_ip)
            except ValueError:
                continue
            if _is_restricted_ip(resolved_ip):
                raise URLValidationError(
                    "Access to private, local, or restricted network addresses is not allowed"
                )

    async def validate(self, url: str) -> str:
        """Validate URL syntax, SSRF safety, and live HTTP reachability."""
        normalized_url = url.strip()
        if not normalized_url:
            raise URLValidationError("URL cannot be empty")

        try:
            parsed = urllib.parse.urlparse(normalized_url)
        except ValueError as exc:
            raise URLValidationError("Invalid URL syntax") from exc
        if parsed.scheme.lower() not in {"http", "https"}:
            raise URLValidationError("URL scheme must be http or https")

        try:
            _ = parsed.port
            hostname = parsed.hostname
        except ValueError as exc:
            raise URLValidationError("URL contains an invalid port") from exc

        if not parsed.netloc or not hostname:
            raise URLValidationError("URL must include a valid hostname")

        initial_hostname = hostname.lower()
        validated_hosts: set[str] = set()
        if not self.allow_private_ips:
            await self._validate_host_ssrf(initial_hostname)
            validated_hosts.add(initial_hostname)

        async def _guard_redirect_request(request: httpx.Request) -> None:
            if self.allow_private_ips:
                return
            if request.url.scheme.lower() not in {"http", "https"}:
                raise URLValidationError("Redirect target must use http or https scheme")
            req_host = (request.url.host or "").lower()
            if not req_host:
                raise URLValidationError("Redirect target must include a valid hostname")
            if req_host not in validated_hosts:
                await self._validate_host_ssrf(req_host)
                validated_hosts.add(req_host)

        try:
            # Why HEAD first, then fallback to streaming GET?
            # 1. HTTP HEAD asks the remote server for status code and headers ONLY,
            #    without downloading the response body. This saves bandwidth and latency
            #    (especially if the target URL points to a large page or media file).
            # 2. However, some web servers, CDNs, and anti-bot firewalls reject HEAD
            #    requests with 405 Method Not Allowed or 403 Forbidden even when the
            #    page loads fine in a browser.
            # 3. When 405 or 403 occurs on HEAD, we fall back to `client.stream("GET", ...)`,
            #    which reads only the response status/headers and immediately closes the
            #    connection stream without downloading the full response body.
            # 4. The `_guard_redirect_request` event hook re-runs our SSRF check on every
            #    redirect hop so a public URL cannot redirect (302) into an internal IP.
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self.timeout,
                transport=self.transport,
                headers={"User-Agent": _USER_AGENT},
                event_hooks={"request": [_guard_redirect_request]},
            ) as client:
                resp = await client.head(normalized_url)
                if resp.status_code in (405, 403):
                    async with client.stream("GET", normalized_url) as get_resp:
                        status_code = get_resp.status_code
                        final_url = get_resp.url
                else:
                    status_code = resp.status_code
                    final_url = resp.url
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise URLValidationError(f"The provided URL is not accessible: {exc}") from exc

        if not self.allow_private_ips:
            final_host = (final_url.host or "").lower()
            if final_host and final_host not in validated_hosts:
                await self._validate_host_ssrf(final_host)

        if status_code >= 400:
            raise URLValidationError(f"The provided URL is not accessible (HTTP {status_code})")

        return normalized_url
