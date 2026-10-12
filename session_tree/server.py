"""FastAPI app serving the dashboard page, the board JSON and session actions."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .board import find_node, find_session
from .terminal import LaunchError, is_session_id, new_session_spec, resume_spec

STATIC_DIR = Path(__file__).with_name("static")
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class FocusBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class NewBody(BaseModel):
    path: str = Field(max_length=1000)


def require_local_post(request: Request) -> None:
    """Block cross-site requests: custom header plus a local Origin when one is sent."""
    if request.headers.get("x-session-tree") != "1":
        raise HTTPException(403, "Missing X-Session-Tree header")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in ALLOWED_HOSTS:
        raise HTTPException(403, "Origin not allowed")


def create_app(service, launcher) -> FastAPI:
    app = FastAPI(title="Session Tree", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_host_only(request: Request, call_next):
        # Rejects DNS-rebinding pages that reach 127.0.0.1 under another name.
        host = urlsplit("//" + (request.headers.get("host") or "")).hostname
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        return await call_next(request)

    def session_or_404(sid: str) -> dict:
        s = find_session(service.snapshot(), sid) if is_session_id(sid) else None
        if s is None:
            raise HTTPException(404, "Unknown session")
        return s

    def run(spec) -> dict:
        try:
            return launcher.launch(spec)
        except LaunchError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/board")
    def board() -> dict:
        return service.snapshot()

    @app.post("/api/sessions/{sid}/focus", dependencies=[Depends(require_local_post)])
    def focus(sid: str, body: FocusBody | None = None) -> dict:
        s = session_or_404(sid)
        if not s["live"]:
            raise HTTPException(409, "Session is closed; use Resume instead")
        title = body.title if body and body.title else s["title"]
        if not title:
            raise HTTPException(409, "Session has no title yet, so its tab cannot be found")
        return launcher.focus(title)

    @app.post("/api/sessions/{sid}/resume", dependencies=[Depends(require_local_post)])
    def resume(sid: str) -> dict:
        s = session_or_404(sid)
        return run(resume_spec(s["cwd"], s["title"] or s["name"], s["id"]))

    @app.post("/api/worktrees/new", dependencies=[Depends(require_local_post)])
    def new_session(body: NewBody) -> dict:
        node = find_node(service.snapshot(), body.path)
        if node is None:
            raise HTTPException(404, "Unknown worktree")
        return run(new_session_spec(node["path"], node["id"]))

    @app.post("/api/worktrees/code", dependencies=[Depends(require_local_post)])
    def open_code(body: NewBody) -> dict:
        node = find_node(service.snapshot(), body.path)
        if node is None:
            raise HTTPException(404, "Unknown worktree")
        try:
            return launcher.open_editor(node["path"])
        except LaunchError as exc:
            raise HTTPException(503, str(exc)) from exc

    return app
