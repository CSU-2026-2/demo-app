import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import AUDIENCE, COOKIE, ISSUER, SESSION_TTL, install_auth
from api.todos import router
from common.database import get_db
from common.models import AuthSession, TodoItem, TodoStep, User
from common.passwords import hash_password, verify_password


@pytest.fixture
def client(monkeypatch, db, session_factory):
    monkeypatch.setenv("JWT_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    app = FastAPI()
    install_auth(app, session_factory)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    db.add(User(username="test-user", password_hash=hash_password("test-password")))
    db.commit()

    @app.get("/health")
    def health():
        return {"ok": True}

    with TestClient(app, base_url="https://testserver") as value:
        yield value


def login(client):
    return client.post("/auth/login", json={"username": "test-user", "password": "test-password"},
                       headers={"Origin": "https://testserver"})


def test_anonymous_access_and_pages(client):
    for path in ("/todos", "/docs", "/openapi.json", "/auth/me"):
        assert client.get(path).status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200


def test_jwt_login_bearer_cookie_and_revocation(client):
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == SESSION_TTL
    token = body["access_token"]
    claims = jwt.decode(token, "s" * 48, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER)
    assert claims["sub"] == str(body["user"]["id"])
    cookie = response.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=strict"):
        assert flag in cookie
    assert client.get("/auth/me").json()["username"] == "test-user"
    assert client.get("/todos").headers["cache-control"] == "no-store"
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/todos", headers=bearer).status_code == 200
    assert client.post("/auth/logout", headers=bearer).status_code == 200
    assert client.get("/todos", headers=bearer).status_code == 401


def test_registration_hashes_password_and_rejects_duplicate(client, db):
    body = {"username": "New-User", "password": "long-password"}
    response = client.post("/auth/register", json=body)
    assert response.status_code == 201
    assert response.json()["user"]["username"] == "new-user"
    stored = db.query(User).filter_by(username="new-user").one()
    assert stored.password_hash != body["password"]
    assert verify_password(body["password"], stored.password_hash)
    assert client.get("/auth/me").json()["id"] == stored.id
    assert client.post("/auth/register", json=body).status_code == 409
    assert client.post("/auth/logout").status_code == 200
    assert client.post("/auth/login", json=body).status_code == 200


@pytest.mark.parametrize("username,password", [("test-user", "wrong-password"), ("unknown", "test-password")])
def test_invalid_credentials(client, username, password):
    assert client.post("/auth/login", json={"username": username, "password": password}).status_code == 401
    assert COOKIE not in client.cookies


@pytest.mark.parametrize("body", [{"username": "ab", "password": "long-password"},
                                  {"username": "valid", "password": "short"},
                                  {"username": "white space", "password": "long-password"}])
def test_registration_validation(client, body):
    assert client.post("/auth/register", json=body).status_code == 422


def test_invalid_jwt_claims_and_signature(client):
    original = login(client).json()["access_token"]
    claims = jwt.decode(original, "s" * 48, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER)
    client.cookies.clear()
    cases = ["invalid", jwt.encode(claims, "x" * 48, algorithm="HS256")]
    for change in ({"exp": int(time.time()) - 1}, {"aud": "other"}, {"iss": "other"}, {"sub": "unknown"}, {"iat": int(time.time()) + 60}):
        cases.append(jwt.encode({**claims, **change}, "s" * 48, algorithm="HS256"))
    missing = dict(claims)
    missing.pop("exp")
    cases.extend([jwt.encode(missing, "s" * 48, algorithm="HS256"), jwt.encode(claims, key="", algorithm="none")])
    for token in cases:
        assert client.get("/todos", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_cross_user_isolation_for_all_operations(client, db, monkeypatch):
    from crud import todos
    monkeypatch.setattr(todos, "send_todo_created_event", lambda **kwargs: None)
    login(client)
    body = {"title": "Private task", "description": "", "completed": False}
    task = client.post("/todos", json=body).json()
    step = TodoStep(todo_id=task["id"], description="Private step", completed=False)
    db.add(step)
    db.commit()
    step_id = step.id
    client.post("/auth/register", json={"username": "second-user", "password": "other-password"})
    assert client.get("/todos").json() == []
    path = f'/todos/{task["id"]}'
    assert client.get(path).status_code == 404
    assert client.put(path, json=body).status_code == 404
    assert client.delete(path).status_code == 404
    assert client.post(path + "/generate").status_code == 404
    assert client.patch(path + f"/steps/{step_id}", json={"completed": True}).status_code == 404
    assert client.post("/todos", json={**body, "owner_id": task["id"]}).status_code == 200
    assert len(client.get("/todos").json()) == 1
    assert db.get(TodoStep, step_id).completed is False


def test_cross_site_and_rate_limit(client):
    assert client.post("/auth/login", json={"username": "test-user", "password": "test-password"},
                       headers={"Origin": "https://another.example"}).status_code == 403
    for _ in range(20):
        assert client.post("/auth/login", json={"username": "wrong-user", "password": "wrong-password"}).status_code == 401
    assert login(client).status_code == 429


def test_auth_configuration_is_required(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    with pytest.raises(RuntimeError):
        install_auth(FastAPI())
