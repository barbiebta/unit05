"""CPU-only regression for Instrumentality's two-ended AV masking/output trim."""

import importlib.util
import sys
import types
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parent
pkg = types.ModuleType("instrumentality_testpkg")
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg

compat = types.ModuleType("instrumentality_testpkg.h3_compat")
compat.ensure_existing_video_compat = lambda: True
sys.modules[compat.__name__] = compat

comfy = types.ModuleType("comfy")
nested = types.ModuleType("comfy.nested_tensor")
utils = types.ModuleType("comfy.utils")


class NestedTensor:
    def __init__(self, xs): self.xs = list(xs)
    def unbind(self): return tuple(self.xs)


nested.NestedTensor = NestedTensor
utils.common_upscale = lambda x, width, height, _method, _crop: torch.nn.functional.interpolate(
    x, size=(height, width), mode="bilinear", align_corners=False
)
comfy.nested_tensor = nested
comfy.utils = utils
sys.modules["comfy"] = comfy
sys.modules["comfy.nested_tensor"] = nested
sys.modules["comfy.utils"] = utils

spec = importlib.util.spec_from_file_location(
    "instrumentality_testpkg.h3_masked_bridge", ROOT / "h3_masked_bridge.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class VideoVAE:
    def encode(self, frames):
        n = int(frames.shape[0])
        t = 2 if n <= 5 else ((n - 5) // 17) * 5 + 2
        return torch.full((1, 24, t, 2, 4), 0.25)

    def decode(self, video):
        n = module._pixel_frames(int(video.shape[2]))
        return torch.full((n, 32, 64, 3), float(video.mean()))


class AudioVAE:
    audio_sample_rate = 32000
    audio_sample_rate_output = 32000

    def encode(self, x):
        t = round(x.shape[1] / 32000 * 40)
        return torch.full((1, 32, 2, t), 0.5)

    def decode(self, audio):
        samples = round(int(audio.shape[-1]) / 40 * 32000)
        return torch.full((1, samples, 2), float(audio.mean()))


def test_instrumentality_locks_both_edges_and_extracts_only_gap():
    # 192 H3 frames = 57 video latent steps / 320 audio steps. With two
    # 39-frame handles, the generation-only center is 114 frames.
    target = {"samples": NestedTensor((
        torch.zeros((1, 24, 57, 2, 4)), torch.zeros((1, 32, 2, 320))
    ))}
    source_frames = torch.rand((124, 32, 64, 3))
    source_audio = {"waveform": torch.rand((1, 2, round(124 / 24 * 32000))), "sample_rate": 32000}
    out, middle, edge = module.MiniMaxH3Instrumentality().prepare(
        target, VideoVAE(), AudioVAE(), source_frames, source_audio,
        source_frames, source_audio, 24.0, 24.0, 39, "disabled"
    )
    assert (middle, edge) == (114, 39)
    video, audio = out["samples"].unbind()
    video_mask, audio_mask = out["noise_mask"].unbind()
    assert torch.all(video_mask[:, :, :12] == 0)
    assert torch.all(video_mask[:, :, -12:] == 0)
    assert torch.all(video_mask[:, :, 12:-12] == 1)
    assert torch.all(audio_mask[..., :65] == 0)
    assert torch.all(audio_mask[..., -65:] == 0)
    assert torch.all(audio_mask[..., 65:-65] == 1)

    images, decoded_audio = module.MiniMaxH3InstrumentalityDecodeAV().decode(
        out, VideoVAE(), AudioVAE()
    )
    gap_images, gap_audio = module.MiniMaxH3InstrumentalityExtractMiddle().extract(
        images, decoded_audio, edge, edge, 24.0
    )
    assert gap_images.shape[0] == 114
    assert gap_audio["waveform"].shape[-1] == round(114 / 24 * 32000)


def test_instrumentality_rejects_video_only_22_frame_av_context():
    target = {"samples": NestedTensor((
        torch.zeros((1, 24, 57, 2, 4)), torch.zeros((1, 32, 2, 320))
    ))}
    frames = torch.rand((124, 32, 64, 3))
    audio = {"waveform": torch.rand((1, 2, round(124 / 24 * 32000))), "sample_rate": 32000}
    try:
        module.MiniMaxH3Instrumentality().prepare(
            target, VideoVAE(), AudioVAE(), frames, audio, frames, audio,
            24.0, 24.0, 22, "disabled"
        )
    except ValueError as exc:
        assert "exact joint H3 AV boundary" in str(exc)
    else:
        raise AssertionError("22-frame non-AV context unexpectedly accepted")
