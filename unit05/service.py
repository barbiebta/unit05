from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .bundle import BundleError, ExtractedBundle, extract_bundle, sha256_file
from .comfy import ComfyClient, ComfyError, build_prompt_graph, collect_output_files
from .config import Config
from .database import Journal
from .delivery import Delivery, DeliveryError
from .metrics import (
    append_render_history,
    collect_environment,
    comfy_timestamps,
    iso_now,
    seconds_between,
)


class ExecutorService:
    def __init__(self, config: Config):
        self.config = config
        self.config.ensure_directories()
        self.journal = Journal(config.database_path)
        self.comfy = ComfyClient(config.comfy_url)
        self.delivery = Delivery(config)
        self.environment = collect_environment(config.comfy_root)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._current: dict[str, Any] | None = None
        self._last_error = ""
        self._started_at = iso_now()
        self._delivery_attempts: dict[str, float] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="unit05-folder-executor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.journal.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._recover_interrupted_job():
                    continue
                self._retry_deliveries()
                candidate = self._next_bundle()
                if candidate:
                    self._claim_and_run(candidate)
                    continue
            except Exception as error:
                self._last_error = f"Executor loop: {error}"
            self._stop.wait(self.config.poll_seconds)

    def _next_bundle(self) -> Path | None:
        candidates = [
            path
            for path in self.config.inputs_dir.iterdir()
            if path.is_file()
            and not path.name.endswith((".partial", ".part", ".tmp"))
            and (path.name.endswith(".dummyjob.zip") or path.suffix.lower() == ".zip")
        ]
        return min(candidates, key=lambda path: (path.stat().st_mtime, path.name)) if candidates else None

    def _claim_and_run(self, incoming: Path) -> None:
        claimed = self.config.working_dir / incoming.name
        if claimed.exists():
            claimed = self.config.working_dir / f"{uuid.uuid4().hex[:8]}_{incoming.name}"
        os.replace(incoming, claimed)
        extract_root = self.config.working_dir / f".extracting-{uuid.uuid4().hex}"
        extracted: ExtractedBundle | None = None
        try:
            extracted = extract_bundle(
                claimed,
                extract_root,
                max_files=self.config.max_archive_files,
                max_uncompressed_bytes=self.config.max_uncompressed_bytes,
            )
            duplicate = self.journal.get(extracted.job_id) or self.journal.get_by_hash(extracted.bundle_hash)
            if duplicate:
                duplicate_path = self.config.archive_dir / f"duplicate_{claimed.name}"
                os.replace(claimed, self._unique_path(duplicate_path))
                shutil.rmtree(extract_root, ignore_errors=True)
                return
            job_root = self.config.working_dir / extracted.job_id
            if job_root.exists():
                raise BundleError(f"Working directory already exists for {extracted.job_id}")
            os.replace(extract_root, job_root)
            extracted = ExtractedBundle(
                job_id=extracted.job_id,
                bundle_hash=extracted.bundle_hash,
                manifest=extracted.manifest,
                prompt=extracted.prompt,
                root=job_root,
            )
            now = iso_now()
            self.journal.insert(
                job_id=extracted.job_id,
                bundle_hash=extracted.bundle_hash,
                bundle_name=claimed.name,
                bundle_path=str(claimed),
                state="validated",
                timestamp=now,
                metadata={"manifest": extracted.manifest, "work_root": str(job_root)},
            )
            self._execute(extracted, claimed)
        except Exception as error:
            job_id = extracted.job_id if extracted else f"invalid-{uuid.uuid4().hex[:8]}"
            self._fail_bundle(job_id, claimed, extract_root, error)

    def _execute(self, bundle: ExtractedBundle, claimed_bundle: Path) -> None:
        job_id = bundle.job_id
        start = iso_now()
        self.journal.update(job_id, start, state="preparing", started_at=start)
        self._set_current(job_id=job_id, title=bundle.manifest.get("title") or job_id, state="preparing")
        staged_inputs: list[Path] = []
        try:
            template = json.loads(self.config.template_path.read_text(encoding="utf-8"))
            staged = build_prompt_graph(
                template=template,
                bundle=bundle,
                comfy_input_dir=self.config.comfy_input_dir,
            )
            staged_inputs = staged.staged_inputs
            graph_path = bundle.root / "exact-api-workflow.json"
            graph_path.write_text(json.dumps(staged.graph, indent=2), encoding="utf-8")
            self._preflight(staged.graph)

            client_id = f"unit05-{job_id}-{uuid.uuid4().hex[:8]}"
            submitted_at = iso_now()
            prompt_id = self.comfy.submit(staged.graph, client_id)
            self.journal.update(
                job_id,
                submitted_at,
                state="rendering",
                prompt_id=prompt_id,
                metadata_json={
                    "manifest": bundle.manifest,
                    "work_root": str(bundle.root),
                    "submitted_at": submitted_at,
                    "staged_inputs": [str(path) for path in staged_inputs],
                },
            )
            self._set_current(job_id=job_id, title=bundle.manifest.get("title") or job_id, state="rendering", prompt_id=prompt_id)

            history = self.comfy.wait_for_completion(
                prompt_id=prompt_id,
                client_id=client_id,
                update=self._record_progress,
                poll_seconds=self.config.history_poll_seconds,
            )
            self._finalize_success(
                bundle=bundle,
                graph=staged.graph,
                staged_inputs=staged_inputs,
                history=history,
                prompt_id=prompt_id,
                started_at=start,
                submitted_at=submitted_at,
            )
        except Exception:
            for staged_input in staged_inputs:
                self._safe_unlink(staged_input, self.config.comfy_input_dir)
            raise
        finally:
            self._clear_current(job_id)

    def _finalize_success(
        self,
        *,
        bundle: ExtractedBundle,
        graph: dict[str, Any],
        staged_inputs: list[Path],
        history: dict[str, Any],
        prompt_id: str,
        started_at: str,
        submitted_at: str,
    ) -> None:
        job_id = bundle.job_id
        comfy_outputs = collect_output_files(history, self.config.comfy_output_dir)
        result_dir = self.config.outbox_dir / job_id
        shutil.rmtree(result_dir, ignore_errors=True)
        result_dir.mkdir(parents=True)
        output_records: list[dict[str, Any]] = []
        for index, source in enumerate(comfy_outputs, start=1):
            name = source.name if len(comfy_outputs) == 1 else f"{index:02d}_{source.name}"
            destination = result_dir / name
            shutil.copy2(source, destination)
            output_records.append(
                {"filename": name, "size": destination.stat().st_size, "sha256": sha256_file(destination)}
            )

        completed_at = iso_now()
        timestamps = comfy_timestamps(history)
        generation = bundle.manifest["generation"]
        attention = next(
            (
                node.get("inputs", {}).get("attention")
                for node in graph.values()
                if node.get("class_type") == "ModelAttentionBackend"
            ),
            "unknown",
        )
        timing = {
            "job_id": job_id,
            "bundle_claimed": started_at,
            "submitted": submitted_at,
            "output_ready": completed_at,
            **timestamps,
            "queue_wait_seconds": seconds_between(submitted_at, timestamps.get("execution_start")),
            "comfy_execution_seconds": seconds_between(
                timestamps.get("execution_start"), timestamps.get("execution_success")
            ),
            "remote_total_seconds": seconds_between(started_at, completed_at),
        }
        render_record = {
            "schema": "unit05.render.v1",
            "job_id": job_id,
            "prompt_id": prompt_id,
            "bundle_sha256": bundle.bundle_hash,
            "workflow": bundle.manifest["workflow"],
            "generation": generation,
            "references": bundle.manifest["references"],
            "outputs": output_records,
            "timing": timing,
            "environment": self.environment,
            "attention_backend": attention,
        }
        (result_dir / "timing.json").write_text(json.dumps(timing, indent=2), encoding="utf-8")
        (result_dir / "render.json").write_text(json.dumps(render_record, indent=2), encoding="utf-8")
        (result_dir / "original-manifest.json").write_text(
            json.dumps(bundle.manifest, indent=2), encoding="utf-8"
        )
        (result_dir / "exact-api-workflow.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")

        append_render_history(
            self.config.logs_dir / "render-history.csv",
            {
                "job_id": job_id,
                "title": bundle.manifest.get("title", ""),
                "prompt_id": prompt_id,
                "created_at": bundle.manifest.get("created_at", ""),
                **timing,
                **generation,
                "attention_backend": attention,
                **self.environment,
            },
        )
        metadata = {
            "manifest": bundle.manifest,
            "work_root": str(bundle.root),
            "submitted_at": submitted_at,
            "staged_inputs": [str(path) for path in staged_inputs],
            "comfy_outputs": [str(path) for path in comfy_outputs],
            "timing": timing,
        }
        self.journal.update(
            job_id,
            completed_at,
            state="output_ready",
            completed_at=completed_at,
            result_dir=str(result_dir),
            metadata_json=metadata,
        )
        self._set_current(
            job_id=job_id,
            title=bundle.manifest.get("title") or job_id,
            state="output_ready",
            prompt_id=prompt_id,
        )
        self._try_delivery(self.journal.get(job_id))

    def _recover_interrupted_job(self) -> bool:
        active_states = {"validated", "preparing", "rendering"}
        active = [job for job in reversed(self.journal.list()) if job["state"] in active_states]
        if not active:
            return False
        job = active[0]
        metadata = job["metadata"]
        work_root = Path(metadata.get("work_root", ""))
        manifest = metadata.get("manifest")
        if not work_root.is_dir() or not isinstance(manifest, dict):
            raise RuntimeError(f"Cannot recover {job['job_id']}: working data is missing")
        prompt_path = work_root / "prompt.txt"
        if not prompt_path.is_file():
            raise RuntimeError(f"Cannot recover {job['job_id']}: prompt.txt is missing")
        bundle = ExtractedBundle(
            job_id=job["job_id"],
            bundle_hash=job["bundle_hash"],
            manifest=manifest,
            prompt=prompt_path.read_text(encoding="utf-8").strip(),
            root=work_root,
        )
        if job["state"] != "rendering":
            self._execute(bundle, Path(job["bundle_path"]))
            return True

        graph_path = work_root / "exact-api-workflow.json"
        if not graph_path.is_file() or not job.get("prompt_id"):
            raise RuntimeError(f"Cannot recover {job['job_id']}: submitted prompt data is missing")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        staged_inputs = [Path(value) for value in metadata.get("staged_inputs", [])]
        prompt_id = str(job["prompt_id"])
        submitted_at = str(metadata.get("submitted_at") or job["updated_at"])
        started_at = str(job.get("started_at") or submitted_at)
        self._set_current(
            job_id=job["job_id"],
            title=manifest.get("title") or job["job_id"],
            state="recovering",
            prompt_id=prompt_id,
        )
        try:
            history = self.comfy.wait_for_completion(
                prompt_id=prompt_id,
                client_id=f"unit05-recovery-{uuid.uuid4().hex[:8]}",
                update=self._record_progress,
                poll_seconds=self.config.history_poll_seconds,
            )
            self._finalize_success(
                bundle=bundle,
                graph=graph,
                staged_inputs=staged_inputs,
                history=history,
                prompt_id=prompt_id,
                started_at=started_at,
                submitted_at=submitted_at,
            )
        finally:
            self._clear_current(job["job_id"])
        return True

    def _record_progress(self, message: dict[str, Any]) -> None:
        event = message.get("event")
        patch: dict[str, Any] = {"event": event}
        if event == "progress":
            value, maximum = message.get("value"), message.get("max")
            patch.update(progress_value=value, progress_max=maximum)
            if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum:
                patch["progress_percent"] = round(float(value) / float(maximum) * 100, 1)
        if event == "executing":
            patch["node"] = message.get("node")
        if message.get("detail"):
            patch["detail"] = message["detail"]
        self._patch_current(**patch)

    def _preflight(self, graph: dict[str, Any]) -> None:
        available = self.comfy.object_info()
        missing = sorted({node.get("class_type") for node in graph.values()} - set(available))
        if missing:
            raise ComfyError(f"Comfy is missing required nodes: {', '.join(str(item) for item in missing)}")

    def _retry_deliveries(self) -> None:
        if not self.delivery.configured:
            return
        now = time.monotonic()
        for job in self.journal.list():
            if job["state"] not in {"output_ready", "delivery_failed"}:
                continue
            if now - self._delivery_attempts.get(job["job_id"], 0) < 30:
                continue
            self._delivery_attempts[job["job_id"]] = now
            self._try_delivery(job)

    def _try_delivery(self, job: dict[str, Any] | None) -> None:
        if not job or not self.delivery.configured:
            return
        job_id = job["job_id"]
        result_dir = Path(job["result_dir"] or "")
        if not result_dir.is_dir():
            self.journal.update(job_id, iso_now(), state="delivery_failed", error="Result directory is missing")
            return
        try:
            self._set_current(job_id=job_id, title=job["metadata"].get("manifest", {}).get("title") or job_id, state="delivering", prompt_id=job.get("prompt_id"))
            destination = self.delivery.send(job_id, result_dir)
            delivered_at = iso_now()
            metadata = dict(job["metadata"])
            metadata["delivery_destination"] = destination
            metadata["delivered_at"] = delivered_at
            self.journal.update(
                job_id,
                delivered_at,
                state="delivered",
                delivered_at=delivered_at,
                error=None,
                metadata_json=metadata,
            )
            self._archive_and_cleanup(self.journal.get(job_id))
        except DeliveryError as error:
            self.journal.update(job_id, iso_now(), state="delivery_failed", error=str(error))
        finally:
            self._clear_current(job_id)

    def _archive_and_cleanup(self, job: dict[str, Any] | None) -> None:
        if not job:
            return
        job_id = job["job_id"]
        bundle_path = Path(job["bundle_path"])
        if bundle_path.is_file():
            archived = self._unique_path(self.config.archive_dir / bundle_path.name)
            os.replace(bundle_path, archived)
            bundle_path = archived
        metadata = job["metadata"]
        for value in metadata.get("staged_inputs", []):
            self._safe_unlink(Path(value), self.config.comfy_input_dir)
        for value in metadata.get("comfy_outputs", []):
            self._safe_unlink(Path(value), self.config.comfy_output_dir)
        work_root = Path(metadata.get("work_root", ""))
        if work_root.is_dir() and work_root.resolve().is_relative_to(self.config.working_dir.resolve()):
            shutil.rmtree(work_root)
        result_dir = Path(job.get("result_dir") or "")
        if result_dir.is_dir() and result_dir.resolve().is_relative_to(self.config.outbox_dir.resolve()):
            shutil.rmtree(result_dir)
        self.journal.update(job_id, iso_now(), state="archived", bundle_path=str(bundle_path))

    def _fail_bundle(self, job_id: str, claimed: Path, extract_root: Path, error: Exception) -> None:
        self._last_error = f"{claimed.name}: {error}"
        failed_bundle = self._unique_path(self.config.failed_dir / claimed.name)
        if claimed.is_file():
            os.replace(claimed, failed_bundle)
        if extract_root.is_dir():
            failed_work = self._unique_path(self.config.failed_dir / f"{failed_bundle.stem}.work")
            os.replace(extract_root, failed_work)
        error_path = self.config.failed_dir / f"{failed_bundle.name}.error.json"
        error_path.write_text(
            json.dumps({"job_id": job_id, "failed_at": iso_now(), "error": str(error)}, indent=2),
            encoding="utf-8",
        )
        existing = self.journal.get(job_id)
        if existing:
            self.journal.update(job_id, iso_now(), state="failed", error=str(error), bundle_path=str(failed_bundle))

    def status(self) -> dict[str, Any]:
        try:
            queue = self.comfy.queue()
            running = [str(item[1]) for item in queue.get("queue_running", []) if len(item) > 1]
            pending = [str(item[1]) for item in queue.get("queue_pending", []) if len(item) > 1]
            comfy_health = {
                "healthy": True,
                "system": self.comfy.health(),
                "queue": {
                    "running_count": len(running),
                    "pending_count": len(pending),
                    "running_prompt_ids": running,
                    "pending_prompt_ids": pending,
                },
            }
        except Exception as error:
            comfy_health = {"healthy": False, "detail": str(error)}
        tailscale = self._tailscale_status()
        with self._lock:
            current = dict(self._current) if self._current else None
        jobs = self.journal.list()
        return {
            "service": "unit05",
            "healthy": bool(self._thread and self._thread.is_alive()),
            "started_at": self._started_at,
            "current": current,
            "folders": {
                "inputs": [path.name for path in sorted(self.config.inputs_dir.iterdir()) if path.is_file()],
                "working": [path.name for path in sorted(self.config.working_dir.iterdir())],
                "outbox": [path.name for path in sorted(self.config.outbox_dir.iterdir())],
                "archive_count": sum(1 for _ in self.config.archive_dir.iterdir()),
                "failed": [path.name for path in sorted(self.config.failed_dir.iterdir())],
            },
            "jobs": jobs,
            "comfy": comfy_health,
            "delivery": self.delivery.health(),
            "tailscale": tailscale,
            "environment": self.environment,
            "last_error": self._last_error,
        }

    def _tailscale_status(self) -> dict[str, Any]:
        executable = shutil.which("tailscale")
        if not executable:
            return {"installed": False, "healthy": False}
        try:
            process = subprocess.run(
                [executable, "status", "--json"], capture_output=True, text=True, timeout=3, check=False
            )
            if process.returncode:
                return {"installed": True, "healthy": False, "detail": process.stderr.strip()}
            payload = json.loads(process.stdout)
            return {
                "installed": True,
                "healthy": payload.get("BackendState") == "Running",
                "self": payload.get("Self", {}),
            }
        except Exception as error:
            return {"installed": True, "healthy": False, "detail": str(error)}

    def _set_current(self, **value: Any) -> None:
        with self._lock:
            self._current = {**value, "updated_at": iso_now()}

    def _patch_current(self, **patch: Any) -> None:
        with self._lock:
            if self._current is not None:
                self._current.update(patch)
                self._current["updated_at"] = iso_now()

    def _clear_current(self, job_id: str) -> None:
        with self._lock:
            if self._current and self._current.get("job_id") == job_id:
                self._current = None

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(1, 10_000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not allocate a unique path for {path}")

    @staticmethod
    def _safe_unlink(path: Path, root: Path) -> None:
        try:
            resolved = path.resolve()
            if resolved.is_file() and resolved.is_relative_to(root.resolve()):
                resolved.unlink()
        except OSError:
            pass
