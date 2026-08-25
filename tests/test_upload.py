from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

import unit05.app as app_module


class StreamingRequest:
    def __init__(self, *chunks: bytes):
        self.chunks = chunks

    async def stream(self):
        for chunk in self.chunks:
            yield chunk


class UploadNameTests(unittest.TestCase):
    def test_accepts_zip_bundle_names(self) -> None:
        self.assertEqual(
            app_module._safe_upload_name("night-queue.dummyjob.zip"),
            "night-queue.dummyjob.zip",
        )
        self.assertEqual(app_module._safe_upload_name("experiment.ZIP"), "experiment.ZIP")

    def test_rejects_non_zip_and_path_names(self) -> None:
        for name in ("prompt.json", "../job.zip", "folder/job.zip", "", ".", ".."):
            with self.subTest(name=name), self.assertRaises(HTTPException):
                app_module._safe_upload_name(name)

    def test_put_streams_then_atomically_queues_without_overwriting(self) -> None:
        original_config = app_module.CONFIG
        with TemporaryDirectory() as temporary:
            try:
                app_module.CONFIG = replace(original_config, root=Path(temporary))
                app_module.CONFIG.ensure_directories()
                first = asyncio.run(
                    app_module.upload_input(
                        "night.dummyjob.zip",
                        StreamingRequest(b"complete-", b"bundle"),
                    )
                )
                with self.assertRaises(HTTPException) as duplicate:
                    asyncio.run(
                        app_module.upload_input(
                            "night.dummyjob.zip",
                            StreamingRequest(b"replacement"),
                        )
                    )

                self.assertEqual(first["bytes"], 15)
                self.assertEqual(duplicate.exception.status_code, 409)
                queued = app_module.CONFIG.inputs_dir / "night.dummyjob.zip"
                self.assertEqual(queued.read_bytes(), b"complete-bundle")
                self.assertFalse(any(app_module.CONFIG.inputs_dir.glob("*.partial")))
            finally:
                app_module.CONFIG = original_config


if __name__ == "__main__":
    unittest.main()
