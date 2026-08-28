from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from unit05.bundle import DIRECTOR_INPUT_SCALING_MODES, extract_bundle
from unit05.comfy import build_prompt_graph

from .helpers import create_bundle, manifest


class AdapterTests(unittest.TestCase):
    def test_maps_job_settings_and_video_trim(self) -> None:
        project_root = Path(__file__).parents[1]
        template = json.loads((project_root / "templates" / "dasiwa_ref2va_api_template.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = extract_bundle(
                create_bundle(root / "job.zip"),
                root / "expanded",
                max_files=100,
                max_uncompressed_bytes=100_000_000,
            )
            with patch(
                "unit05.comfy.probe_media",
                return_value={"duration": 11.0, "width": 768, "height": 1344, "fps": 24, "has_video": True, "has_audio": True},
            ):
                staged = build_prompt_graph(template=template, bundle=bundle, comfy_input_dir=root / "comfy-input")

            director = next(node for node in staged.graph.values() if node["class_type"] == "MiniMaxH3Director")
            self.assertEqual(director["inputs"]["width"], 768)
            self.assertEqual(director["inputs"]["height"], 1344)
            self.assertEqual(director["inputs"]["duration"], 5)
            self.assertEqual(director["inputs"]["ref_image_size"], "match")
            timeline = json.loads(director["inputs"]["timeline_data"])
            self.assertEqual(timeline["resolution"]["input_scaling"], "Auto")
            self.assertEqual(timeline["items"][0]["trim_start"], 2.0)
            self.assertEqual(timeline["items"][0]["trim_end"], 7.0)
            self.assertNotIn("thumbnail", timeline["items"][0])
            noise = next(node for node in staged.graph.values() if node["class_type"] == "RandomNoise")
            self.assertEqual(noise["inputs"]["noise_seed"], 123456789)
            schedulers = [node for node in staged.graph.values() if node["class_type"] == "BasicScheduler"]
            self.assertTrue(schedulers)
            self.assertTrue(all(node["inputs"]["steps"] == 25 for node in schedulers))
            shifts = [node for node in staged.graph.values() if node["class_type"] == "MiniMaxH3SigmaShift"]
            self.assertTrue(all(node["inputs"]["shift_video"] == 12 for node in shifts))
            self.assertEqual(len(staged.staged_inputs), 1)

    def test_passes_every_director_input_scaling_mode_unchanged(self) -> None:
        project_root = Path(__file__).parents[1]
        template = json.loads((project_root / "templates" / "dasiwa_ref2va_api_template.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, mode in enumerate(sorted(DIRECTOR_INPUT_SCALING_MODES)):
                with self.subTest(mode=mode):
                    generation = manifest()["generation"]
                    generation["input_scaling"] = mode
                    bundle = extract_bundle(
                        create_bundle(root / f"mode-{index}.zip", override={"generation": generation}),
                        root / f"expanded-{index}",
                        max_files=100,
                        max_uncompressed_bytes=100_000_000,
                    )
                    with patch(
                        "unit05.comfy.probe_media",
                        return_value={"duration": 11.0, "width": 768, "height": 1344, "fps": 24, "has_video": True, "has_audio": True},
                    ):
                        staged = build_prompt_graph(
                            template=template,
                            bundle=bundle,
                            comfy_input_dir=root / f"comfy-input-{index}",
                        )
                    director = next(node for node in staged.graph.values() if node["class_type"] == "MiniMaxH3Director")
                    timeline = json.loads(director["inputs"]["timeline_data"])
                    builder_state = json.loads(director["inputs"]["builder_state"])
                    self.assertEqual(timeline["resolution"]["input_scaling"], mode)
                    self.assertEqual(builder_state["resolution"]["input_scaling"], mode)


if __name__ == "__main__":
    unittest.main()
