"""FastAPI app: API routers, static frontend, background scheduler."""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, get_settings
from .db import init_db
from .routers import career, finance

FRONTEND_DIR = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = None
    if get_settings().enable_scheduler:
        from .scheduler import run_forever
        task = asyncio.create_task(run_forever())
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    app = FastAPI(title="Personal Dashboard", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(finance.router, prefix="/api/finance", tags=["finance"])
    app.include_router(career.router, prefix="/api/career", tags=["career"])

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    if Path(FRONTEND_DIR).is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
