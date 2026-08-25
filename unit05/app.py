from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status as http_status
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


@app.put("/api/inputs/{filename}", status_code=http_status.HTTP_201_CREATED)
async def upload_input(filename: str, request: Request) -> dict[str, object]:
    safe_name = _safe_upload_name(filename)
    target = CONFIG.inputs_dir / safe_name
    partial = target.with_name(f".{target.name}.{uuid4().hex}.partial")
    byte_count = 0
    try:
        with partial.open("xb") as output:
            async for chunk in request.stream():
                if chunk:
                    output.write(chunk)
                    byte_count += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if byte_count == 0:
            raise HTTPException(status_code=400, detail="Empty uploads are not accepted")
        try:
            os.link(partial, target)
        except FileExistsError as error:
            raise HTTPException(status_code=409, detail=f"{safe_name} already exists") from error
        partial.unlink()
        return {"filename": safe_name, "bytes": byte_count, "state": "queued"}
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _safe_upload_name(filename: str) -> str:
    if not filename or filename in {".", ".."} or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Unit05 accepts .zip job bundles only")
    return filename


def main() -> None:
    parser = argparse.ArgumentParser(description="Run unit05")
    parser.add_argument("--host", default=CONFIG.host)
    parser.add_argument("--port", type=int, default=CONFIG.port)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
