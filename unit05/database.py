from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    bundle_hash TEXT NOT NULL,
    bundle_name TEXT NOT NULL,
    bundle_path TEXT NOT NULL,
    state TEXT NOT NULL,
    prompt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    delivered_at TEXT,
    result_dir TEXT,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_bundle_hash ON jobs(bundle_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_state_updated ON jobs(state, updated_at);
"""


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._decode(row) if row else None

    def get_by_hash(self, bundle_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM jobs WHERE bundle_hash = ?", (bundle_hash,)).fetchone()
        return self._decode(row) if row else None

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._decode(row) for row in rows]

    def insert(
        self,
        *,
        job_id: str,
        bundle_hash: str,
        bundle_name: str,
        bundle_path: str,
        state: str,
        timestamp: str,
        metadata: dict[str, Any],
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO jobs (
                    job_id, bundle_hash, bundle_name, bundle_path, state,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    bundle_hash,
                    bundle_name,
                    bundle_path,
                    state,
                    timestamp,
                    timestamp,
                    json.dumps(metadata, separators=(",", ":")),
                ),
            )

    def update(self, job_id: str, timestamp: str, **fields: Any) -> None:
        allowed = {
            "bundle_path",
            "state",
            "prompt_id",
            "started_at",
            "completed_at",
            "delivered_at",
            "result_dir",
            "error",
            "metadata_json",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown journal fields: {sorted(unknown)}")
        if "metadata_json" in fields and not isinstance(fields["metadata_json"], str):
            fields["metadata_json"] = json.dumps(fields["metadata_json"], separators=(",", ":"))
        fields["updated_at"] = timestamp
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [fields[name] for name in fields]
        values.append(job_id)
        with self._lock, self._connection:
            self._connection.execute(f"UPDATE jobs SET {assignments} WHERE job_id = ?", values)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        try:
            value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            value["metadata"] = {}
        return value
