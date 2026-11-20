from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from unit05.bundle import DIRECTOR_INPUT_SCALING_MODES, BundleError, extract_bundle

from .helpers import create_bundle, manifest


class BundleTests(unittest.TestCase):
    def test_extracts_committed_dummyplug_v2_fixture(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "dummyplug-v2-smoke.zip"
        with tempfile.TemporaryDirectory() as temporary:
            extracted = extract_bundle(
                fixture,
                Path(temporary) / "expanded",
                max_files=100,
                max_uncompressed_bytes=100_000_000,
            )
            self.assertEqual(extracted.manifest["schema"], "dummyplug.h3-job.v2")
            self.assertEqual(extracted.job_id, "Rabbitcr_89abcdef")
            self.assertEqual([item["kind"] for item in extracted.manifest["references"]], ["image", "image", "video"])

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

    def test_accepts_legacy_v1_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extracted = extract_bundle(
                create_bundle(root / "legacy.zip", schema="dummyplug.h3-job.v1"),
                root / "expanded",
                max_files=100,
                max_uncompressed_bytes=100_000_000,
            )
            self.assertEqual(extracted.manifest["schema"], "dummyplug.h3-job.v1")

    def test_rejects_fractional_or_non_divisible_dimensions(self) -> None:
        invalid_values = ((768.5, 1344), (770, 1344), (768, 1345))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (width, height) in enumerate(invalid_values):
                with self.subTest(width=width, height=height):
                    generation = manifest()["generation"]
                    generation.update({"width": width, "height": height})
                    with self.assertRaises(BundleError):
                        extract_bundle(
                            create_bundle(root / f"invalid-size-{index}.zip", override={"generation": generation}),
                            root / f"invalid-size-{index}",
                            max_files=100,
                            max_uncompressed_bytes=100_000_000,
                        )

    def test_rejects_fractional_duration_instead_of_truncating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation = manifest()["generation"]
            generation["duration"] = 5.5
            with self.assertRaisesRegex(BundleError, "generation.duration must be an integer"):
                extract_bundle(
                    create_bundle(root / "fractional-duration.zip", override={"generation": generation}),
                    root / "expanded",
                    max_files=100,
                    max_uncompressed_bytes=100_000_000,
                )

    def test_v2_requires_job_description_and_complete_prompt_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, override in enumerate((
                {"job_desc": ""},
                {"prompt_policy": {}},
                {"prompt_policy": {"allCharactersTransgenderWomen": True, "allowedVocabulary": {}}},
            )):
                with self.subTest(override=override):
                    with self.assertRaises(BundleError):
                        extract_bundle(
                            create_bundle(root / f"bad-policy-{index}.zip", override=override),
                            root / f"bad-policy-{index}",
                            max_files=100,
                            max_uncompressed_bytes=100_000_000,
                        )

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
                schema="dummyplug.h3-job.v1",
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

    def test_accepts_every_director_input_scaling_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, mode in enumerate(sorted(DIRECTOR_INPUT_SCALING_MODES)):
                with self.subTest(mode=mode):
                    generation = manifest()["generation"]
                    generation["input_scaling"] = mode
                    extracted = extract_bundle(
                        create_bundle(root / f"mode-{index}.zip", override={"generation": generation}),
                        root / f"expanded-{index}",
                        max_files=100,
                        max_uncompressed_bytes=100_000_000,
                    )
                    self.assertEqual(extracted.manifest["generation"]["input_scaling"], mode)

    def test_normalizes_all_dummyplug_legacy_scaling_options(self) -> None:
        expected = {
            "match": ("match", "Auto"),
            "max": ("max", "Auto"),
            "native": ("match", "Auto"),
            "fit": ("match", "Fit"),
            "fill": ("match", "Fill and crop"),
            "720": ("match", "Auto"),
            "1024": ("match", "Auto"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (legacy, normalized) in enumerate(expected.items()):
                with self.subTest(legacy=legacy):
                    generation = manifest()["generation"]
                    generation.pop("ref_image_size")
                    generation["input_scaling"] = legacy
                    extracted = extract_bundle(
                        create_bundle(
                            root / f"legacy-{index}.zip",
                            schema="dummyplug.h3-job.v1",
                            override={"generation": generation},
                        ),
                        root / f"legacy-expanded-{index}",
                        max_files=100,
                        max_uncompressed_bytes=100_000_000,
                    )
                    actual = extracted.manifest["generation"]
                    self.assertEqual((actual["ref_image_size"], actual["input_scaling"]), normalized)

    def test_rejects_unknown_input_scaling_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation = manifest()["generation"]
            generation["input_scaling"] = "native-ish"
            with self.assertRaisesRegex(BundleError, "input_scaling must be one of"):
                extract_bundle(
                    create_bundle(root / "invalid.zip", override={"generation": generation}),
                    root / "expanded",
                    max_files=100,
                    max_uncompressed_bytes=100_000_000,
                )


if __name__ == "__main__":
    unittest.main()
