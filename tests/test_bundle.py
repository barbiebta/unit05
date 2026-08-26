from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from unit05.bundle import BundleError, extract_bundle

from .helpers import create_bundle, manifest


class BundleTests(unittest.TestCase):
    def test_extracts_and_verifies_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = create_bundle(root / "job.dummyjob.zip")
            extracted = extract_bundle(
                bundle_path,
                root / "expanded",
                max_files=100,
                max_uncompressed_bytes=100_000_000,
            )
            self.assertEqual(extracted.job_id, "job-test-001")
            self.assertEqual(extracted.manifest["generation"]["width"], 768)
            self.assertTrue((extracted.root / "assets" / "video-1__performance.mov").is_file())

    def test_rejects_zip_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "unsafe.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape", b"bad")
            with self.assertRaises(BundleError):
                extract_bundle(path, root / "expanded", max_files=100, max_uncompressed_bytes=1_000_000)

    def test_normalizes_legacy_combined_scaling_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_generation = manifest()["generation"]
            legacy_generation.pop("ref_image_size")
            legacy_generation["input_scaling"] = "match"
            bundle_path = create_bundle(
                root / "legacy.dummyjob.zip",
                override={"generation": legacy_generation},
            )

            extracted = extract_bundle(
                bundle_path,
                root / "expanded",
                max_files=100,
                max_uncompressed_bytes=100_000_000,
            )

            generation = extracted.manifest["generation"]
            self.assertEqual(generation["ref_image_size"], "match")
            self.assertEqual(generation["input_scaling"], "Auto")


if __name__ == "__main__":
    unittest.main()
