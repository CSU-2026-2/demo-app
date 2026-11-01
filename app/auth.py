"""Вход под общей учётной записью и подписанная cookie с ограниченным сроком."""

import hashlib
import hmac
import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

COOKIE = "demo_session"
SESSION_TTL = 12 * 60 * 60


class Credentials(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1000)


def install_auth(app: FastAPI) -> None:
    username = os.environ["AUTH_USERNAME"]
    password = os.environ["AUTH_PASSWORD"]
    secret = os.environ["AUTH_SESSION_SECRET"]
    if not username or not password or len(secret) < 32:
        raise RuntimeError("Set AUTH_USERNAME, AUTH_PASSWORD and AUTH_SESSION_SECRET (32+ characters)")
    secure = os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
    key = secret.encode()
    attempts = OrderedDict()
    attempts_lock = Lock()

    def sign(payload):
        return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()

    def authenticated(request):
        token = request.cookies.get(COOKIE, "")
        try:
            issued, nonce, signature = token.split(".")
            age = time.time() - int(issued)
            return 0 <= age < SESSION_TTL and hmac.compare_digest(signature, sign(f"{issued}.{nonce}"))
        except (ValueError, TypeError):
            return False

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        public = path in {"/login", "/auth/login", "/health", "/metrics"} or path.startswith("/static/")
        if not public and not authenticated(request):
            if path == "/":
                return RedirectResponse("/login", status_code=303)
            return JSONResponse({"detail": "Требуется вход"}, status_code=401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            scheme = "https" if secure else request.url.scheme
            expected_origin = f"{scheme}://{request.url.netloc}"
            if origin and origin != expected_origin:
                return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
        response = await call_next(request)
        if not path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if authenticated(request):
            return RedirectResponse("/", status_code=303)
        return FileResponse(Path(__file__).parent / "static" / "login.html")

    @app.post("/auth/login", include_in_schema=False)
    def login(body: Credentials, request: Request):
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with attempts_lock:
            count, start = attempts.get(address, (0, now))
            if now - start >= 60:
                count, start = 0, now
            if count >= 10:
                return JSONResponse({"detail": "Слишком много попыток. Подождите минуту."}, status_code=429, headers={"Retry-After": "60"})
            attempts[address] = (count + 1, start)
            attempts.move_to_end(address)
            if len(attempts) > 4096:
                attempts.popitem(last=False)
        valid_user = hmac.compare_digest(body.username.encode(), username.encode())
        valid_password = hmac.compare_digest(hashlib.sha256(body.password.encode()).digest(), hashlib.sha256(password.encode()).digest())
        if not (valid_user and valid_password):
            return JSONResponse({"detail": "Неверный логин или пароль"}, status_code=401)
        payload = f"{int(time.time())}.{secrets.token_hex(16)}"
        response = JSONResponse({"username": username})
        response.set_cookie(COOKIE, f"{payload}.{sign(payload)}", max_age=SESSION_TTL,
                            httponly=True, secure=secure, samesite="strict", path="/")
        return response

    @app.post("/auth/logout", include_in_schema=False)
    def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=secure, httponly=True, samesite="strict")
        return response
