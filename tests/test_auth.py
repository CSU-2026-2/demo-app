import hashlib
import hmac
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import COOKIE, SESSION_TTL, install_auth


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_USERNAME", "test-user")
    monkeypatch.setenv("AUTH_PASSWORD", "test-password")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    app = FastAPI()
    install_auth(app)

    @app.get("/todos")
    def tasks():
        return [{"id": 1}]

    @app.get("/health")
    def health():
        return {"ok": True}

    with TestClient(app, base_url="https://testserver") as value:
        yield value


def login(client):
    return client.post("/auth/login", json={"username": "test-user", "password": "test-password"},
                       headers={"Origin": "https://testserver"})


def test_anonymous_access_and_login_page(client):
    assert client.get("/todos").status_code == 401
    assert client.post("/todos").status_code == 401
    assert client.get("/docs").status_code == 401
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/health").status_code == 200
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert 'autocomplete="current-password"' in client.get("/login").text


def test_login_cookie_and_logout(client):
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=strict", f"Max-Age={SESSION_TTL}"):
        assert flag in cookie
    assert client.get("/todos").json() == [{"id": 1}]
    assert client.get("/todos").headers["cache-control"] == "no-store"
    assert client.get("/login", follow_redirects=False).status_code == 303
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/todos").status_code == 401


@pytest.mark.parametrize("username,password", [("test-user", "wrong"), ("wrong", "test-password"), ("тест", "пароль")])
def test_invalid_credentials(client, username, password):
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert COOKIE not in client.cookies


def test_expired_and_tampered_sessions(client):
    def signed(issued):
        payload = f"{issued}.nonce"
        signature = hmac.new(b"s" * 48, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"
    for token in ("invalid", signed(int(time.time()) - SESSION_TTL - 1),
                  signed(int(time.time()) + 60), signed(int(time.time()))[:-1] + "z"):
        response = client.get("/todos", headers={"Cookie": f"{COOKIE}={token}"})
        assert response.status_code == 401


def test_cross_site_login_and_logout_are_rejected(client):
    response = client.post("/auth/login", json={"username": "test-user", "password": "test-password"},
                           headers={"Origin": "https://another.example"})
    assert response.status_code == 403
    assert login(client).status_code == 200
    assert client.post("/auth/logout", headers={"Origin": "https://another.example"}).status_code == 403
    assert client.get("/todos").status_code == 200


def test_login_attempts_are_limited(client):
    for _ in range(10):
        assert client.post("/auth/login", json={"username": "x", "password": "x"}).status_code == 401
    assert login(client).status_code == 429


def test_auth_configuration_is_required(monkeypatch):
    monkeypatch.setenv("AUTH_USERNAME", "demo")
    monkeypatch.setenv("AUTH_PASSWORD", "")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "short")
    with pytest.raises(RuntimeError):
        install_auth(FastAPI())
