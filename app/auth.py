"""Регистрация пользователей, JWT и отзыв сессий при выходе."""

import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from common.database import SessionLocal, get_db
from common.models import AuthSession, User
from common.passwords import hash_password, verify_password

COOKIE = "demo_access_token"
SESSION_TTL = 60 * 60
ISSUER = "demo-app"
AUDIENCE = "demo-app-api"


class Credentials(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        return value.lower()


def get_current_user_id(request: Request) -> int:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, "Требуется вход", headers={"WWW-Authenticate": "Bearer"})
    return user_id


def install_auth(app: FastAPI, session_factory=SessionLocal) -> None:
    secret = os.environ["JWT_SECRET_KEY"]
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET_KEY must contain at least 32 characters")
    secure = os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
    app.state.auth_session_factory = session_factory
    attempts, attempts_lock = OrderedDict(), Lock()
    dummy_hash = hash_password(secrets.token_urlsafe(24))

    def authenticate(request):
        authorization = request.headers.get("authorization")
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer":
                return None
        else:
            token = request.cookies.get(COOKIE, "")
        try:
            claims = jwt.decode(token, secret, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE,
                                options={"require": ["sub", "exp", "iat", "iss", "aud", "jti"]})
            user_id = int(claims["sub"])
            with app.state.auth_session_factory() as db:
                session = db.get(AuthSession, claims["jti"])
                if session and session.user_id == user_id and session.expires_at > time.time() and db.get(User, user_id):
                    return user_id, session.id
        except (jwt.InvalidTokenError, ValueError, TypeError):
            pass
        return None

    def limit_attempts(request):
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with attempts_lock:
            count, start = attempts.get(address, (0, now))
            if now - start >= 60:
                count, start = 0, now
            if count >= 20:
                raise HTTPException(429, "Слишком много попыток. Подождите минуту.", headers={"Retry-After": "60"})
            attempts[address] = count + 1, start
            attempts.move_to_end(address)
            if len(attempts) > 4096:
                attempts.popitem(last=False)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        public = path in {"/login", "/register", "/auth/login", "/auth/register", "/health", "/metrics"} or path.startswith("/static/")
        if not public:
            identity = await run_in_threadpool(authenticate, request)
            if not identity:
                if path == "/":
                    return RedirectResponse("/login", status_code=303)
                return JSONResponse({"detail": "Требуется вход"}, status_code=401, headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
            request.state.user_id, request.state.session_id = identity
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            scheme = "https" if secure else request.url.scheme
            if origin and origin != f"{scheme}://{request.url.netloc}":
                return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
        response = await call_next(request)
        if not path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/login", include_in_schema=False)
    @app.get("/register", include_in_schema=False)
    def login_page():
        return FileResponse(Path(__file__).parent / "static" / "login.html")

    def issue_token(db, user):
        now, session_id = int(time.time()), secrets.token_hex(16)
        expires = now + SESSION_TTL
        token = jwt.encode({"sub": str(user.id), "iat": now, "exp": expires, "jti": session_id,
                            "iss": ISSUER, "aud": AUDIENCE}, secret, algorithm="HS256")
        db.query(AuthSession).filter(AuthSession.expires_at <= now).delete()
        db.add(AuthSession(id=session_id, user_id=user.id, expires_at=expires))
        db.commit()
        response = JSONResponse({"access_token": token, "token_type": "bearer", "expires_in": SESSION_TTL,
                                 "user": {"id": user.id, "username": user.username}})
        response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=secure, samesite="strict", path="/")
        response.delete_cookie("demo_session", path="/")
        return response

    @app.post("/auth/register", status_code=201)
    def register(body: Credentials, request: Request, db: Session = Depends(get_db)):
        limit_attempts(request)
        user = User(username=body.username, password_hash=hash_password(body.password))
        try:
            db.add(user)
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Этот логин уже занят")
        response = issue_token(db, user)
        response.status_code = 201
        return response

    @app.post("/auth/login")
    def login(body: Credentials, request: Request, db: Session = Depends(get_db)):
        limit_attempts(request)
        user = db.query(User).filter_by(username=body.username).first()
        valid = verify_password(body.password, user.password_hash if user else dummy_hash)
        if not user or not valid:
            raise HTTPException(401, "Неверный логин или пароль", headers={"WWW-Authenticate": "Bearer"})
        return issue_token(db, user)

    @app.get("/auth/me")
    def me(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
        user = db.get(User, user_id)
        return {"id": user.id, "username": user.username}

    @app.post("/auth/logout")
    def logout(request: Request, db: Session = Depends(get_db)):
        db.query(AuthSession).filter_by(id=request.state.session_id).delete()
        db.commit()
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=secure, httponly=True, samesite="strict")
        return response
