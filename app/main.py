from api import todos, health, metrics
from db import database, models
from fastapi import FastAPI, Request
from prometheus_client import Counter
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from auth import install_auth
from migrate import initialize_schema
import os

initialize_schema(database.engine, os.environ["AUTH_USERNAME"], os.environ["AUTH_PASSWORD"])

app = FastAPI()
install_auth(app)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(static_dir / "index.html")

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests processed by the FastAPI service",
    ["method", "path", "status"],
)

@app.middleware("http")
async def count_http_requests(request: Request, call_next):
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path_template = getattr(route, "path", request.url.path)
        http_requests_total.labels(
            method=request.method,
            path=path_template,
            status=str(status_code),
        ).inc()

app.include_router(todos.router)
app.include_router(health.router)
app.include_router(metrics.router)
