"""Loopback-first dashboard and same-origin API."""

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..cli import envelope
from ..core import Digest, DigestError


class TaskInput(BaseModel):
    title: str = Field(min_length=1)
    agent: str = "Unassigned"
    due_at: str | None = None
    priority: int = Field(default=2, ge=1, le=3)


def create_app(service: Digest | None = None) -> FastAPI:
    service = service or Digest()
    app = FastAPI(title="Daily Digest", version="0.1.0")
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "Cross-origin requests are not allowed",
                    },
                },
                status_code=403,
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(DigestError)
    async def handle_error(request, exc):
        return JSONResponse(envelope(error=exc), status_code=404 if exc.exit_code == 3 else 400)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/digest")
    def get_digest(date: str | None = None, agent: str | None = None):
        return envelope(service.digest(date, agent), service.warnings)

    @app.post("/api/scan")
    def scan():
        return envelope(service.scan(), service.warnings)

    @app.get("/api/files/{file_id}")
    def preview(file_id: str):
        return envelope(service.preview(file_id))

    @app.get("/api/files/{file_id}/raw")
    def raw(file_id: str):
        preview = service.preview(file_id)
        path = service.allowed(Path(preview["path"]))
        if preview["kind"] == "image":
            return FileResponse(path)
        return FileResponse(
            path,
            filename=path.name,
            media_type="application/octet-stream",
            content_disposition_type="attachment",
        )

    @app.post("/api/tasks")
    def add_task(task: TaskInput):
        return envelope(service.add_task(**task.model_dump()))

    @app.post("/api/tasks/{task_id}/done")
    def complete(task_id: str):
        return envelope(service.complete_task(task_id))

    @app.get("/api/report")
    def report(date: str | None = None):
        result = service.report(day=date)
        return Response(
            result["markdown"],
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{Path(result["path"]).name}"'},
        )

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
