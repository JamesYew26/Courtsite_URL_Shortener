from __future__ import annotations

import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import URLRepository
from app.validator import URLValidator


@pytest.fixture()
def repo(tmp_path) -> URLRepository:
    repository = URLRepository(str(tmp_path / "test.db"))
    repository.init_db()
    return repository


@pytest.fixture()
def validator(monkeypatch: pytest.MonkeyPatch) -> URLValidator:
    def fake_getaddrinfo(host: str, port: object) -> list:
        normalized = host.lower().rstrip(".")
        if normalized == "rebind.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 0))]
        if normalized.endswith(".invalid"):
            raise socket.gaierror("Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    async def handler(request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        if host == "dead.example.com":
            return httpx.Response(404)
        if host == "unreachable.example.com":
            raise httpx.ConnectError("Connection refused")
        if host in {"www.courtsite.my", "courtsite.my", "ok.example.com"}:
            return httpx.Response(200)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    return URLValidator(timeout=5.0, transport=transport)


@pytest.fixture()
def client(repo: URLRepository, validator: URLValidator) -> TestClient:
    app = create_app(repo=repo, validator=validator)
    with TestClient(app) as test_client:
        yield test_client
