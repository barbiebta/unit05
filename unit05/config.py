from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


@dataclass(frozen=True)
class Config:
    root: Path
    comfy_url: str
    comfy_root: Path
    comfy_input_dir: Path
    comfy_output_dir: Path
    template_path: Path
    host: str
    port: int
    poll_seconds: float
    history_poll_seconds: float
    max_archive_files: int
    max_uncompressed_bytes: int
    local_output_dir: Path | None
    sftp_host: str
    sftp_port: int
    sftp_user: str
    sftp_key: Path | None
    sftp_dir: str

    @classmethod
    def from_env(cls) -> "Config":
        local_output = os.environ.get("UNIT05_OUTPUT_LOCAL_DIR", "").strip()
        sftp_key = os.environ.get("UNIT05_OUTPUT_SFTP_KEY", "").strip()
        return cls(
            root=_env_path("UNIT05_ROOT", "/workspace/unit05/data"),
            comfy_url=os.environ.get("UNIT05_COMFY_URL", "http://127.0.0.1:18188").rstrip("/"),
            comfy_root=_env_path("UNIT05_COMFY_ROOT", "/workspace/ComfyUI"),
            comfy_input_dir=_env_path("UNIT05_COMFY_INPUT_DIR", "/workspace/input"),
            comfy_output_dir=_env_path("UNIT05_COMFY_OUTPUT_DIR", "/workspace/output"),
            template_path=_env_path(
                "UNIT05_TEMPLATE",
                "/workspace/unit05/templates/dasiwa_ref2va_api_template.json",
            ),
            host=os.environ.get("UNIT05_HOST", "127.0.0.1"),
            port=int(os.environ.get("UNIT05_PORT", "18765")),
            poll_seconds=float(os.environ.get("UNIT05_POLL_SECONDS", "2")),
            history_poll_seconds=float(os.environ.get("UNIT05_HISTORY_POLL_SECONDS", "2")),
            max_archive_files=int(os.environ.get("UNIT05_MAX_ARCHIVE_FILES", "256")),
            max_uncompressed_bytes=int(os.environ.get("UNIT05_MAX_UNCOMPRESSED_BYTES", str(2 * 1024**3))),
            local_output_dir=Path(local_output).expanduser().resolve() if local_output else None,
            sftp_host=os.environ.get("UNIT05_OUTPUT_SFTP_HOST", "").strip(),
            sftp_port=int(os.environ.get("UNIT05_OUTPUT_SFTP_PORT", "22")),
            sftp_user=os.environ.get("UNIT05_OUTPUT_SFTP_USER", "").strip(),
            sftp_key=Path(sftp_key).expanduser().resolve() if sftp_key else None,
            sftp_dir=os.environ.get("UNIT05_OUTPUT_SFTP_DIR", "").strip(),
        )

    @property
    def inputs_dir(self) -> Path:
        return self.root / "inputs"

    @property
    def working_dir(self) -> Path:
        return self.root / "working"

    @property
    def outbox_dir(self) -> Path:
        return self.root / "outbox"

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive"

    @property
    def failed_dir(self) -> Path:
        return self.root / "failed"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def database_path(self) -> Path:
        return self.root / "executor.sqlite"

    def ensure_directories(self) -> None:
        for path in (
            self.root,
            self.inputs_dir,
            self.working_dir,
            self.outbox_dir,
            self.archive_dir,
            self.failed_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if self.local_output_dir:
            self.local_output_dir.mkdir(parents=True, exist_ok=True)
