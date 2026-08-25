from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .config import Config
from .service import ExecutorService


CONFIG = Config.from_env()
SERVICE = ExecutorService(CONFIG)
DASHBOARD = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    SERVICE.start()
    try:
        yield
    finally:
        SERVICE.stop()


app = FastAPI(title="unit05", version="0.1.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD


@app.get("/health")
def health() -> dict[str, object]:
    status = SERVICE.status()
    return {
        "healthy": status["healthy"],
        "comfy": status["comfy"].get("healthy"),
        "delivery": status["delivery"].get("healthy"),
        "current": status["current"],
    }


@app.get("/api/status")
def status() -> dict[str, object]:
    return SERVICE.status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run unit05")
    parser.add_argument("--host", default=CONFIG.host)
    parser.add_argument("--port", type=int, default=CONFIG.port)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
