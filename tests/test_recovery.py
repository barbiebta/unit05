from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from unit05.config import Config
from unit05.metrics import iso_now
from unit05.service import ExecutorService

from .helpers import manifest


class RecoveryTests(unittest.TestCase):
    def test_completed_comfy_prompt_is_packaged_without_resubmission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            comfy_root = root / "ComfyUI"
            comfy_input = root / "input"
            comfy_output = root / "output"
            template = root / "template.json"
            for path in (comfy_root, comfy_input, comfy_output):
                path.mkdir()
            template.write_text("{}")
            config = Config(
                root=root / "executor-data",
                comfy_url="http://127.0.0.1:1",
                comfy_root=comfy_root,
                comfy_input_dir=comfy_input,
                comfy_output_dir=comfy_output,
                template_path=template,
                host="127.0.0.1",
                port=18765,
                poll_seconds=0.01,
                history_poll_seconds=0.01,
                max_archive_files=100,
                max_uncompressed_bytes=100_000_000,
                local_output_dir=None,
                sftp_host="",
                sftp_port=22,
                sftp_user="",
                sftp_key=None,
                sftp_dir="",
            )
            service = ExecutorService(config)
            job_id = "recovery-test-001"
            work_root = config.working_dir / job_id
            work_root.mkdir()
            (work_root / "prompt.txt").write_text("six-section prompt")
            graph = {"1": {"class_type": "ModelAttentionBackend", "inputs": {"attention": "test"}}}
            (work_root / "exact-api-workflow.json").write_text(json.dumps(graph))
            output = comfy_output / "render.webm"
            output.write_bytes(b"completed-render")
            created = iso_now()
            job_manifest = manifest(job_id)
            service.journal.insert(
                job_id=job_id,
                bundle_hash="a" * 64,
                bundle_name=f"{job_id}.dummyjob.zip",
                bundle_path=str(config.working_dir / f"{job_id}.dummyjob.zip"),
                state="validated",
                timestamp=created,
                metadata={"manifest": job_manifest, "work_root": str(work_root)},
            )
            service.journal.update(
                job_id,
                created,
                state="rendering",
                prompt_id="prompt-recovered",
                started_at=created,
                metadata_json={
                    "manifest": job_manifest,
                    "work_root": str(work_root),
                    "submitted_at": created,
                    "staged_inputs": [],
                },
            )
            history = {
                "status": {
                    "completed": True,
                    "status_str": "success",
                    "messages": [
                        ["execution_start", {"timestamp": 1_000}],
                        ["execution_success", {"timestamp": 2_000}],
                    ],
                },
                "outputs": {
                    "9": {
                        "images": [
                            {"filename": output.name, "subfolder": "", "type": "output"}
                        ]
                    }
                },
            }
            service.comfy.wait_for_completion = Mock(return_value=history)

            self.assertTrue(service._recover_interrupted_job())
            recovered = service.journal.get(job_id)
            self.assertEqual(recovered["state"], "output_ready")
            self.assertEqual(service.comfy.wait_for_completion.call_count, 1)
            self.assertTrue((config.outbox_dir / job_id / "render.webm").is_file())
            record = json.loads((config.outbox_dir / job_id / "render.json").read_text())
            self.assertEqual(record["prompt_id"], "prompt-recovered")
            service.journal.close()


if __name__ == "__main__":
    unittest.main()
