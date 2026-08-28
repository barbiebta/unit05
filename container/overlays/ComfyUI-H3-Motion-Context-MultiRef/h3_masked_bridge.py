"""Two-ended MiniMax H3 AV bridge using PR #15375-style latent noise masks.

This node is intentionally small: it prepares the target latent only. Sampling is
still performed by normal ComfyUI sampler nodes. The first and last source
windows are VAE-encoded directly into the target H3 AV latent and protected by a
nested denoise mask (0 = preserve, 1 = generate). On ComfyUI builds without
native PR #15375 support, this repo's lazy compatibility layer is enabled only
when the node executes.
"""

from __future__ import annotations

import logging

import torch


try:
    import torchaudio
except ImportError:
    torchaudio = None


_LOG = logging.getLogger("h3_motion_context.masked_bridge")
FPS = 24.0
AUDIO_HZ = 40.0
FRAME_PER_TOKEN = (1, 4, 4, 4, 4)


def largest_h3_video_run(frames):
    n = int(frames)
    if n < 5:
        return 0
    return 5 + ((n - 5) // 17) * 17


def _require_h3_mask_support():
    from .h3_compat import ensure_existing_video_compat
    # Native PR #15375 support wins, including the Aug-15+ no-preprocess-hook
    # architecture. The compatibility orchestrator validates the full path.
    ensure_existing_video_compat()


def _pixel_frames(latent_t):
    return sum(FRAME_PER_TOKEN[k % 5] for k in range(int(latent_t)))


def _streams_from_latent(latent):
    samples = latent["samples"]
    if hasattr(samples, "unbind"):
        parts = list(samples.unbind())
    elif isinstance(samples, (tuple, list)):
        parts = list(samples)
    else:
        raise ValueError(
            "h3_masked_bridge: expected a MiniMax H3 AV latent, got %r" % type(samples)
        )
    if len(parts) < 2:
        raise ValueError("h3_masked_bridge: expected joint H3 video+audio latent streams")
    video, audio = parts[0], parts[1]
    if video.ndim == 4:
        video = video.unsqueeze(0)
    if audio.ndim == 3:
        audio = audio.unsqueeze(0)
    if video.ndim != 5:
        raise ValueError(
            "h3_masked_bridge: video latent must be [B,C,T,H,W], got %s"
            % (tuple(video.shape),)
        )
    if audio.ndim != 4:
        raise ValueError(
            "h3_masked_bridge: audio latent must be [B,C,2,T], got %s"
            % (tuple(audio.shape),)
        )
    return video, audio


def _resize_images(images, width, height, crop, chunk=32):
    import comfy.utils
    if int(images.shape[0]) <= chunk:
        x = images[..., :3].movedim(-1, 1)
        x = comfy.utils.common_upscale(x, width, height, "lanczos", crop)
        return x.movedim(1, -1)
    out = []
    for start in range(0, int(images.shape[0]), chunk):
        part = images[start:start + chunk, ..., :3].movedim(-1, 1)
        part = comfy.utils.common_upscale(part, width, height, "lanczos", crop)
        out.append(part.movedim(1, -1))
    return torch.cat(out, dim=0)


def _cfr_index_map(frame_count, source_fps, device, target_fps=FPS):
    source_fps = float(source_fps)
    if source_fps <= 0.0:
        raise ValueError("h3_masked_bridge: source_fps must be > 0")
    n = int(frame_count)
    if n < 1:
        raise ValueError("h3_masked_bridge: source video has no frames")
    out_n = max(1, int(round(n * float(target_fps) / source_fps)))
    if out_n == n and abs(source_fps - target_fps) < 1e-6:
        return torch.arange(n, device=device, dtype=torch.long)
    i = torch.arange(out_n, device=device, dtype=torch.float64)
    t = (i + 0.5) / float(target_fps)
    src = torch.round(t * source_fps - 0.5).to(torch.long)
    return src.clamp_(0, n - 1)


def _stereo_first_batch(waveform, label):
    if getattr(waveform, "ndim", 0) != 3:
        raise ValueError(
            "h3_masked_bridge: %s waveform must be [B,C,L], got %s"
            % (label, tuple(getattr(waveform, "shape", ())))
        )
    waveform = waveform[:1]
    channels = int(waveform.shape[1])
    if channels == 1:
        return waveform.repeat(1, 2, 1)
    if channels == 2:
        return waveform
    raise ValueError(
        "h3_masked_bridge: %s has %d channels; downmix to stereo first"
        % (label, channels)
    )


def _resample_waveform(waveform, source_sr, target_sr, label):
    source_sr = int(source_sr)
    target_sr = int(target_sr)
    if source_sr == target_sr:
        return waveform
    if torchaudio is None:
        raise RuntimeError(
            "h3_masked_bridge: %s is %d Hz but %d Hz is required and torchaudio is unavailable"
            % (label, source_sr, target_sr)
        )
    return torchaudio.functional.resample(waveform, source_sr, target_sr)


def _canonical_audio(audio, target_sr, frame_count, label):
    if audio is None:
        raise ValueError("h3_masked_bridge: %s is required" % label)
    waveform = _stereo_first_batch(audio["waveform"], label)
    waveform = _resample_waveform(
        waveform, int(audio["sample_rate"]), int(target_sr), label
    )
    want = int(round(int(frame_count) / FPS * int(target_sr)))
    have = int(waveform.shape[-1])
    if have > want:
        waveform = waveform[..., :want]
    elif have < want:
        waveform = torch.nn.functional.pad(waveform, (0, want - have))
    return {"waveform": waveform, "sample_rate": int(target_sr)}


def _validate_preserve_frames(requested, start_available, end_available, target_frames):
    n = int(requested)
    if n < 5:
        raise ValueError("h3_masked_bridge: preserve_frames must be at least 5")
    if largest_h3_video_run(n) != n:
        raise ValueError(
            "h3_masked_bridge: preserve_frames must be an exact H3 video run "
            "(5, 22, 39, 56, ...); got %d" % n
        )
    if int(start_available) < n:
        raise ValueError(
            "h3_masked_bridge: start clip has only %d canonical 24fps frames; need %d"
            % (int(start_available), n)
        )
    if int(end_available) < n:
        raise ValueError(
            "h3_masked_bridge: end clip has only %d canonical 24fps frames; need %d"
            % (int(end_available), n)
        )
    if 2 * n >= int(target_frames):
        raise ValueError(
            "h3_masked_bridge: target must contain a non-empty generated middle; "
            "target=%d, preserved=%d+%d" % (int(target_frames), n, n)
        )
    return n


def _encode_video_window(vae, frames, width, height, crop, n, label):
    resized = _resize_images(frames, width, height, crop)
    encoded = vae.encode(resized)
    if getattr(encoded, "ndim", 0) != 5:
        raise ValueError(
            "h3_masked_bridge: %s video VAE returned %s; expected [B,C,T,H,W]"
            % (label, tuple(getattr(encoded, "shape", ())))
        )
    steps = int(encoded.shape[2])
    covered = _pixel_frames(steps)
    if covered != int(n):
        raise RuntimeError(
            "h3_masked_bridge: %s %d-frame window encoded to %d video latent "
            "steps covering %d frames; refusing a phase-shifted seam"
            % (label, int(n), steps, covered)
        )
    return encoded[:1], steps


def _encode_audio_window(audio_vae, canonical_audio, n, side, label):
    vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
    samples = int(round(int(n) / FPS * vae_sr))
    waveform = canonical_audio["waveform"]
    if int(waveform.shape[-1]) < samples:
        raise ValueError(
            "h3_masked_bridge: %s audio is shorter than the selected %d-frame window"
            % (label, int(n))
        )
    if side == "tail":
        window = waveform[..., -samples:]
    elif side == "head":
        window = waveform[..., :samples]
    else:
        raise ValueError("h3_masked_bridge: internal invalid audio side %r" % side)

    encoded = audio_vae.encode(window.movedim(1, -1))
    if getattr(encoded, "ndim", 0) != 4:
        raise ValueError(
            "h3_masked_bridge: %s audio VAE returned %s; expected [B,C,2,T]"
            % (label, tuple(getattr(encoded, "shape", ())))
        )

    exact_steps = int(n) / FPS * AUDIO_HZ
    expected = int(round(exact_steps))
    if abs(exact_steps - expected) > 1e-9:
        _LOG.warning(
            "h3_masked_bridge: %d preserved frames end between H3 audio ticks "
            "(%.6f -> %d steps); 39/90/141/... are exact AV boundaries",
            int(n), exact_steps, expected,
        )
    got = int(encoded.shape[-1])
    if got < expected:
        raise RuntimeError(
            "h3_masked_bridge: %s needs %d audio latent steps but VAE produced %d"
            % (label, expected, got)
        )
    if got > expected:
        # Preserve the physical side of the source window. A tail is end-aligned;
        # a head is start-aligned.
        encoded = encoded[..., -expected:] if side == "tail" else encoded[..., :expected]
    return encoded[:1], expected


class MiniMaxH3MaskedAVBridge:
    """Freeze AV windows at both ends of an H3 target; denoise only the middle."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "Target MiniMax H3 AV latent. For example, use a 192-frame H3 latent."
                }),
                "vae": ("VAE", {"tooltip": "MiniMax H3 video VAE."}),
                "audio_vae": ("VAE", {"tooltip": "MiniMax H3 audio VAE."}),
                "start_frames": ("IMAGE", {
                    "tooltip": "Full first source clip or a frame batch containing its ending. The final preserve_frames are frozen at the bridge start."
                }),
                "start_audio": ("AUDIO", {
                    "tooltip": "Audio matching start_frames. The final preserved interval is frozen at the bridge start."
                }),
                "end_frames": ("IMAGE", {
                    "tooltip": "Full second source clip or a frame batch containing its beginning. The first preserve_frames are frozen at the bridge end."
                }),
                "end_audio": ("AUDIO", {
                    "tooltip": "Audio matching end_frames. The first preserved interval is frozen at the bridge end."
                }),
                "start_fps": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001,
                    "tooltip": "Frame rate represented by start_frames. CFR input is converted deterministically to H3 24 fps."
                }),
                "end_fps": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001,
                    "tooltip": "Frame rate represented by end_frames. CFR input is converted deterministically to H3 24 fps."
                }),
                "preserve_frames": ("INT", {
                    "default": 39, "min": 5, "max": 9999,
                    "tooltip": "Exact H3 run length: 5, 22, 39, 56, ... . 39 is recommended because it is exactly 1.625 s / 65 audio latent steps."
                }),
                "crop": (["disabled", "center"], {"default": "center"}),
            }
        }

    RETURN_TYPES = ("LATENT", "INT", "INT")
    RETURN_NAMES = ("latent", "middle_frames", "preserve_frames")
    FUNCTION = "prepare"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = (
        "Create a true two-ended H3 AV masked bridge. The ending of the first "
        "clip and beginning of the second clip are encoded into the target latent "
        "and protected with PR #15375-style video+audio denoise masks; only the "
        "middle is generated. 0 = preserve, 1 = denoise."
    )

    def prepare(
        self,
        latent,
        vae,
        audio_vae,
        start_frames,
        start_audio,
        end_frames,
        end_audio,
        start_fps=24.0,
        end_fps=24.0,
        preserve_frames=39,
        crop="center",
    ):
        _require_h3_mask_support()

        target_video, target_audio = _streams_from_latent(latent)
        if int(target_video.shape[0]) != 1 or int(target_audio.shape[0]) != 1:
            raise ValueError("h3_masked_bridge: MiniMax H3 bridge currently supports batch size 1")

        target_frames = _pixel_frames(int(target_video.shape[2]))
        if largest_h3_video_run(target_frames) != target_frames:
            raise RuntimeError(
                "h3_masked_bridge: target latent covers %d frames, which is not an exact "
                "H3 video run (5, 22, 39, 56, ...)" % target_frames
            )
        expected_target_audio = int(round(target_frames / FPS * AUDIO_HZ))
        if int(target_audio.shape[-1]) != expected_target_audio:
            raise RuntimeError(
                "h3_masked_bridge: target has %d audio steps for %d frames; expected %d"
                % (int(target_audio.shape[-1]), target_frames, expected_target_audio)
            )

        if getattr(start_frames, "ndim", 0) != 4 or int(start_frames.shape[0]) < 1:
            raise ValueError("h3_masked_bridge: start_frames must be IMAGE [N,H,W,C]")
        if getattr(end_frames, "ndim", 0) != 4 or int(end_frames.shape[0]) < 1:
            raise ValueError("h3_masked_bridge: end_frames must be IMAGE [N,H,W,C]")

        start_idx = _cfr_index_map(int(start_frames.shape[0]), float(start_fps), start_frames.device, FPS)
        end_idx = _cfr_index_map(int(end_frames.shape[0]), float(end_fps), end_frames.device, FPS)
        n = _validate_preserve_frames(
            preserve_frames, int(start_idx.numel()), int(end_idx.numel()), target_frames
        )

        width = int(target_video.shape[4]) * 16
        height = int(target_video.shape[3]) * 16
        start_tail = start_frames.index_select(0, start_idx[-n:])
        end_head = end_frames.index_select(0, end_idx[:n])

        start_video, start_vsteps = _encode_video_window(
            vae, start_tail, width, height, crop, n, "start tail"
        )
        end_video, end_vsteps = _encode_video_window(
            vae, end_head, width, height, crop, n, "end head"
        )
        if start_vsteps != end_vsteps:
            raise RuntimeError(
                "h3_masked_bridge: start/end video windows encoded to different lengths (%d vs %d)"
                % (start_vsteps, end_vsteps)
            )
        if start_vsteps * 2 >= int(target_video.shape[2]):
            raise ValueError("h3_masked_bridge: preserved video windows consume the whole target")

        # A valid H3 target and valid H3 preserved run should automatically have
        # the same temporal VAE phase at the suffix. Keep this assertion explicit:
        # it is what makes a standalone encoded 39-frame head safe at the end.
        suffix_video_step = int(target_video.shape[2]) - end_vsteps
        if suffix_video_step % 5 != 0:
            raise RuntimeError(
                "h3_masked_bridge: suffix begins at video latent step %d (phase %d); "
                "refusing to place a standalone encoded suffix out of H3 temporal phase"
                % (suffix_video_step, suffix_video_step % 5)
            )

        vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
        start_canonical = _canonical_audio(start_audio, vae_sr, int(start_idx.numel()), "start_audio")
        end_canonical = _canonical_audio(end_audio, vae_sr, int(end_idx.numel()), "end_audio")
        start_audio_lat, start_asteps = _encode_audio_window(
            audio_vae, start_canonical, n, "tail", "start tail"
        )
        end_audio_lat, end_asteps = _encode_audio_window(
            audio_vae, end_canonical, n, "head", "end head"
        )
        if start_asteps != end_asteps:
            raise RuntimeError(
                "h3_masked_bridge: start/end audio windows encoded to different lengths (%d vs %d)"
                % (start_asteps, end_asteps)
            )
        if start_asteps * 2 >= int(target_audio.shape[-1]):
            raise ValueError("h3_masked_bridge: preserved audio windows consume the whole target")

        out_video = target_video.clone()
        out_audio = target_audio.clone()

        sv = start_video.to(device=out_video.device, dtype=out_video.dtype)
        ev = end_video.to(device=out_video.device, dtype=out_video.dtype)
        sa = start_audio_lat.to(device=out_audio.device, dtype=out_audio.dtype)
        ea = end_audio_lat.to(device=out_audio.device, dtype=out_audio.dtype)

        if tuple(sv.shape[1:2] + sv.shape[3:]) != tuple(out_video.shape[1:2] + out_video.shape[3:]):
            raise ValueError(
                "h3_masked_bridge: start video latent shape %s does not match target %s"
                % (tuple(sv.shape), tuple(out_video.shape))
            )
        if tuple(ev.shape[1:2] + ev.shape[3:]) != tuple(out_video.shape[1:2] + out_video.shape[3:]):
            raise ValueError(
                "h3_masked_bridge: end video latent shape %s does not match target %s"
                % (tuple(ev.shape), tuple(out_video.shape))
            )
        if tuple(sa.shape[1:3]) != tuple(out_audio.shape[1:3]):
            raise ValueError(
                "h3_masked_bridge: start audio latent shape %s does not match target %s"
                % (tuple(sa.shape), tuple(out_audio.shape))
            )
        if tuple(ea.shape[1:3]) != tuple(out_audio.shape[1:3]):
            raise ValueError(
                "h3_masked_bridge: end audio latent shape %s does not match target %s"
                % (tuple(ea.shape), tuple(out_audio.shape))
            )

        out_video[:, :, :start_vsteps] = sv
        out_video[:, :, -end_vsteps:] = ev
        out_audio[..., :start_asteps] = sa
        out_audio[..., -end_asteps:] = ea

        # PR #15375 semantics: 0 = preserve existing latent, 1 = denoise/generate.
        # The compatibility layer/native H3 implementation snaps video masks to
        # 2x2 latent patches and audio masks to audio latent frames.
        video_mask = torch.ones(
            (1, 1, int(out_video.shape[2]), int(out_video.shape[3]), int(out_video.shape[4])),
            device=out_video.device,
            dtype=torch.float32,
        )
        audio_mask = torch.ones(
            (1, 1, int(out_audio.shape[2]), int(out_audio.shape[3])),
            device=out_audio.device,
            dtype=torch.float32,
        )
        video_mask[:, :, :start_vsteps] = 0.0
        video_mask[:, :, -end_vsteps:] = 0.0
        audio_mask[..., :start_asteps] = 0.0
        audio_mask[..., -end_asteps:] = 0.0

        import comfy.nested_tensor
        out = latent.copy()
        out["samples"] = comfy.nested_tensor.NestedTensor((out_video, out_audio))
        out["noise_mask"] = comfy.nested_tensor.NestedTensor((video_mask, audio_mask))

        middle_frames = target_frames - 2 * n
        _LOG.info(
            "h3_masked_bridge: target %d frames @ %dx%d; preserve %d frames at each end "
            "(%d video steps / %d audio steps each); generate middle %d frames (%.3fs)",
            target_frames, width, height, n, start_vsteps, start_asteps,
            middle_frames, middle_frames / FPS,
        )
        return (out, middle_frames, n)


class MiniMaxH3Instrumentality(MiniMaxH3MaskedAVBridge):
    """Named, exact-AV version of the bilateral H3 gap-filler."""

    @classmethod
    def INPUT_TYPES(cls):
        data = MiniMaxH3MaskedAVBridge.INPUT_TYPES()
        # H3 video runs alone may be 5/22/39/56/... frames, but an AV seam must
        # also land on H3's 40-Hz audio grid. At 24 fps that means 39/90/141/...
        # frames. The previous bridge exposes the more permissive research
        # interface; Instrumentality intentionally rejects fractional AV joins.
        data["required"]["preserve_frames"] = ("INT", {
            "default": 39, "min": 39, "max": 9999,
            "tooltip": "Frames locked at EACH edge. Exact joint H3 AV boundaries only: 39, 90, 141, 192, ... . 39 = 1.625 s."
        })
        data["required"]["crop"] = (["disabled", "center"], {
            "default": "disabled",
            "tooltip": "Use disabled when the paired clips already match the target framing; center is an explicit fallback crop."
        })
        return data

    RETURN_TYPES = ("LATENT", "INT", "INT")
    RETURN_NAMES = ("latent", "middle_frames", "edge_context_frames")
    FUNCTION = "prepare"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = (
        "Instrumentality: a genuine two-ended H3 AV gap fill. The tail of the "
        "left clip and head of the right clip are VAE-encoded directly into the "
        "target latent and locked with mask=0. H3 denoises only the middle. "
        "This node accepts only exact shared H3 video/audio boundaries."
    )

    def prepare(
        self,
        latent,
        vae,
        audio_vae,
        start_frames,
        start_audio,
        end_frames,
        end_audio,
        start_fps=24.0,
        end_fps=24.0,
        preserve_frames=39,
        crop="disabled",
    ):
        exact = float(preserve_frames) / FPS * AUDIO_HZ
        if abs(exact - round(exact)) > 1e-9:
            raise ValueError(
                "Instrumentality: preserve_frames must be an exact joint H3 AV "
                "boundary (39, 90, 141, 192, ...), not %d" % int(preserve_frames)
            )
        return super().prepare(
            latent, vae, audio_vae, start_frames, start_audio, end_frames,
            end_audio, start_fps, end_fps, preserve_frames, crop,
        )


def _video_steps_for_frames(frames):
    frames = int(frames)
    for steps in range(1, 100000):
        covered = _pixel_frames(steps)
        if covered == frames:
            return steps
        if covered > frames:
            break
    raise ValueError(
        "Instrumentality: %d frames are not an exact H3 video run" % frames
    )


def _compress_video_time(video, target_steps):
    """Linear temporal resampling without altering channels or spatial axes."""
    target_steps = int(target_steps)
    if int(video.shape[2]) == target_steps:
        return video
    dtype = video.dtype
    return torch.nn.functional.interpolate(
        video.float(),
        size=(target_steps, int(video.shape[3]), int(video.shape[4])),
        mode="trilinear",
        align_corners=False,
    ).to(dtype=dtype)


def _compress_audio_time(audio, target_steps):
    """Linear temporal resampling while keeping H3's two audio rows separate."""
    target_steps = int(target_steps)
    if int(audio.shape[-1]) == target_steps:
        return audio
    b, c, rows, source_steps = audio.shape
    flat = audio.reshape(b * c * rows, 1, source_steps).float()
    flat = torch.nn.functional.interpolate(
        flat, size=target_steps, mode="linear", align_corners=False
    )
    return flat.reshape(b, c, rows, target_steps).to(dtype=audio.dtype)


class MiniMaxH3CompressedContextBridge:
    """Compress broad AV context into short locked latent boundaries."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "A 124-frame H3 AV target latent; its native generated middle is 46 frames."
                }),
                "vae": ("VAE",),
                "audio_vae": ("VAE",),
                "start_frames": ("IMAGE", {
                    "tooltip": "Ending context. The final source_context_frames are encoded and temporally compressed."
                }),
                "start_audio": ("AUDIO",),
                "end_frames": ("IMAGE", {
                    "tooltip": "Beginning context. The first source_context_frames are encoded and temporally compressed."
                }),
                "end_audio": ("AUDIO",),
                "start_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001}),
                "end_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001}),
                "source_context_frames": ("INT", {
                    "default": 90, "min": 39, "max": 9999,
                    "tooltip": "Exact joint H3 AV source run, normally 90 frames."
                }),
                "compressed_context_frames": ("INT", {
                    "default": 39, "min": 39, "max": 9999,
                    "tooltip": "Exact joint H3 AV boundary represented after latent temporal compression."
                }),
                "crop": (["disabled", "center"], {"default": "disabled"}),
            }
        }

    RETURN_TYPES = ("LATENT", "INT", "INT", "INT")
    RETURN_NAMES = (
        "latent", "native_middle_frames", "source_context_frames",
        "compressed_context_frames",
    )
    FUNCTION = "prepare"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = (
        "Encode broad context at both sides of a hole, compress each context in "
        "latent time, and lock the compressed representations around an H3 "
        "generated middle. Designed for the 90 -> 39 frame mutation experiment."
    )

    def prepare(
        self,
        latent,
        vae,
        audio_vae,
        start_frames,
        start_audio,
        end_frames,
        end_audio,
        start_fps=24.0,
        end_fps=24.0,
        source_context_frames=90,
        compressed_context_frames=39,
        crop="disabled",
    ):
        _require_h3_mask_support()
        source_n = int(source_context_frames)
        compressed_n = int(compressed_context_frames)
        for label, value in (("source", source_n), ("compressed", compressed_n)):
            if largest_h3_video_run(value) != value:
                raise ValueError(
                    "Instrumentality: %s context %d is not an exact H3 video run"
                    % (label, value)
                )
            exact_audio = value / FPS * AUDIO_HZ
            if abs(exact_audio - round(exact_audio)) > 1e-9:
                raise ValueError(
                    "Instrumentality: %s context %d is not a joint H3 AV boundary"
                    % (label, value)
                )

        target_video, target_audio = _streams_from_latent(latent)
        target_frames = _pixel_frames(int(target_video.shape[2]))
        if int(target_audio.shape[-1]) != int(round(target_frames / FPS * AUDIO_HZ)):
            raise ValueError("Instrumentality: target H3 AV streams have mismatched durations")

        start_idx = _cfr_index_map(
            int(start_frames.shape[0]), float(start_fps), start_frames.device, FPS
        )
        end_idx = _cfr_index_map(
            int(end_frames.shape[0]), float(end_fps), end_frames.device, FPS
        )
        if int(start_idx.numel()) < source_n or int(end_idx.numel()) < source_n:
            raise ValueError(
                "Instrumentality: compressed context needs %d frames on both sides"
                % source_n
            )

        width = int(target_video.shape[4]) * 16
        height = int(target_video.shape[3]) * 16
        start_pixels = start_frames.index_select(0, start_idx[-source_n:])
        end_pixels = end_frames.index_select(0, end_idx[:source_n])
        start_video, _ = _encode_video_window(
            vae, start_pixels, width, height, crop, source_n, "compressed start context"
        )
        end_video, _ = _encode_video_window(
            vae, end_pixels, width, height, crop, source_n, "compressed end context"
        )

        vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
        start_canonical = _canonical_audio(
            start_audio, vae_sr, int(start_idx.numel()), "start_audio"
        )
        end_canonical = _canonical_audio(
            end_audio, vae_sr, int(end_idx.numel()), "end_audio"
        )
        start_audio_lat, _ = _encode_audio_window(
            audio_vae, start_canonical, source_n, "tail", "compressed start context"
        )
        end_audio_lat, _ = _encode_audio_window(
            audio_vae, end_canonical, source_n, "head", "compressed end context"
        )

        edge_vsteps = _video_steps_for_frames(compressed_n)
        edge_asteps = int(round(compressed_n / FPS * AUDIO_HZ))
        start_video = _compress_video_time(start_video, edge_vsteps)
        end_video = _compress_video_time(end_video, edge_vsteps)
        start_audio_lat = _compress_audio_time(start_audio_lat, edge_asteps)
        end_audio_lat = _compress_audio_time(end_audio_lat, edge_asteps)

        if edge_vsteps * 2 >= int(target_video.shape[2]):
            raise ValueError("Instrumentality: compressed video contexts consume target")
        if edge_asteps * 2 >= int(target_audio.shape[-1]):
            raise ValueError("Instrumentality: compressed audio contexts consume target")
        suffix_video_step = int(target_video.shape[2]) - edge_vsteps
        if suffix_video_step % 5 != 0:
            raise ValueError(
                "Instrumentality: compressed suffix begins at invalid H3 phase %d"
                % (suffix_video_step % 5)
            )

        out_video = target_video.clone()
        out_audio = target_audio.clone()
        out_video[:, :, :edge_vsteps] = start_video.to(out_video)
        out_video[:, :, -edge_vsteps:] = end_video.to(out_video)
        out_audio[..., :edge_asteps] = start_audio_lat.to(out_audio)
        out_audio[..., -edge_asteps:] = end_audio_lat.to(out_audio)

        video_mask = torch.ones(
            (1, 1, int(out_video.shape[2]), int(out_video.shape[3]), int(out_video.shape[4])),
            device=out_video.device, dtype=torch.float32,
        )
        audio_mask = torch.ones(
            (1, 1, int(out_audio.shape[2]), int(out_audio.shape[3])),
            device=out_audio.device, dtype=torch.float32,
        )
        video_mask[:, :, :edge_vsteps] = 0.0
        video_mask[:, :, -edge_vsteps:] = 0.0
        audio_mask[..., :edge_asteps] = 0.0
        audio_mask[..., -edge_asteps:] = 0.0

        import comfy.nested_tensor
        out = latent.copy()
        out["samples"] = comfy.nested_tensor.NestedTensor((out_video, out_audio))
        out["noise_mask"] = comfy.nested_tensor.NestedTensor((video_mask, audio_mask))
        native_middle = target_frames - 2 * compressed_n
        _LOG.info(
            "Instrumentality compressed context: %d -> %d frames at each edge; "
            "native generated center %d frames",
            source_n, compressed_n, native_middle,
        )
        return (out, native_middle, source_n, compressed_n)


class MiniMaxH3CompressGeneratedMiddleLatent:
    """Extract and temporally compress a sampled AV middle before decoding."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {"tooltip": "Sampled compressed-context bridge latent."}),
                "edge_context_frames": ("INT", {"default": 39, "min": 39}),
                "output_frames": ("INT", {"default": 39, "min": 5}),
            }
        }

    RETURN_TYPES = ("LATENT", "INT", "INT")
    RETURN_NAMES = ("compressed_middle_latent", "source_middle_frames", "output_frames")
    FUNCTION = "compress"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = (
        "Remove the locked edge latents, then resample the denoised middle in "
        "latent time before either VAE is decoded."
    )

    def compress(self, latent, edge_context_frames=39, output_frames=39):
        video, audio = _streams_from_latent(latent)
        edge_n = int(edge_context_frames)
        output_n = int(output_frames)
        edge_vsteps = _video_steps_for_frames(edge_n)
        edge_asteps = int(round(edge_n / FPS * AUDIO_HZ))
        if edge_vsteps * 2 >= int(video.shape[2]) or edge_asteps * 2 >= int(audio.shape[-1]):
            raise ValueError("Instrumentality: edge contexts consume sampled latent")

        middle_video = video[:, :, edge_vsteps:-edge_vsteps]
        middle_audio = audio[..., edge_asteps:-edge_asteps]
        # The free slice begins at a nonzero five-token VAE phase, so decoding
        # its step count as though it began at phase zero would misreport its
        # pixel duration. Derive duration from the complete target timeline.
        source_middle_frames = _pixel_frames(int(video.shape[2])) - 2 * edge_n
        target_vsteps = _video_steps_for_frames(output_n)
        target_asteps_exact = output_n / FPS * AUDIO_HZ
        if abs(target_asteps_exact - round(target_asteps_exact)) > 1e-9:
            raise ValueError(
                "Instrumentality: output_frames must be an exact joint H3 AV boundary"
            )
        target_asteps = int(round(target_asteps_exact))
        middle_video = _compress_video_time(middle_video, target_vsteps)
        middle_audio = _compress_audio_time(middle_audio, target_asteps)

        import comfy.nested_tensor
        out = latent.copy()
        out["samples"] = comfy.nested_tensor.NestedTensor((middle_video, middle_audio))
        out.pop("noise_mask", None)
        _LOG.info(
            "Instrumentality compressed generated middle: %d frames -> %d frames "
            "(%d -> %d video steps; audio -> %d steps)",
            source_middle_frames, output_n, int(video.shape[2]) - 2 * edge_vsteps,
            target_vsteps, target_asteps,
        )
        return (out, source_middle_frames, output_n)


class MiniMaxH3InstrumentalityDecodeAV:
    """Decode a joint H3 AV latent into Comfy IMAGE and AUDIO objects."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {"tooltip": "Sampled joint MiniMax H3 AV latent."}),
                "vae": ("VAE", {"tooltip": "MiniMax H3 video VAE."}),
                "audio_vae": ("VAE", {"tooltip": "MiniMax H3 audio VAE."}),
            }
        }

    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("images", "audio")
    FUNCTION = "decode"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = (
        "Decode the complete Instrumentality target timeline. Connect its output "
        "to Instrumentality Extract Middle to save only the newly generated gap."
    )

    def decode(self, latent, vae, audio_vae):
        video, audio = _streams_from_latent(latent)
        images = vae.decode(video)
        if getattr(images, "ndim", 0) == 5 and int(images.shape[0]) == 1:
            images = images[0]
        if getattr(images, "ndim", 0) != 4:
            raise ValueError(
                "Instrumentality: video VAE decode returned %s, expected frame batch"
                % (tuple(getattr(images, "shape", ())),)
            )
        if int(images.shape[-1]) in (3, 4):
            images = images[..., :3]
        elif int(images.shape[1]) in (3, 4):
            images = images.movedim(1, -1)[..., :3]
        else:
            raise ValueError(
                "Instrumentality: cannot find RGB axis in decoded frames %s"
                % (tuple(images.shape),)
            )

        waveform = audio_vae.decode(audio).movedim(-1, 1)
        # Match the normalization used by the existing continuation decoder.
        std = torch.std(waveform, dim=[1, 2], keepdim=True) * 5.0
        std[std < 1.0] = 1.0
        waveform = waveform / std
        sample_rate = int(getattr(
            audio_vae, "audio_sample_rate_output",
            getattr(audio_vae, "audio_sample_rate", 44100),
        ))
        return (images, {"waveform": waveform, "sample_rate": sample_rate})


class MiniMaxH3InstrumentalityExtractMiddle:
    """Remove the two locked handles from an Instrumentality decode."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "Complete decoded Instrumentality target timeline."}),
                "audio": ("AUDIO", {"tooltip": "Audio decoded from the same target latent."}),
                "left_context_frames": ("INT", {"tooltip": "Connect Instrumentality edge_context_frames."}),
                "right_context_frames": ("INT", {"tooltip": "Connect Instrumentality edge_context_frames."}),
                "frame_rate": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001,
                    "tooltip": "H3 native rate is 24 fps."
                }),
            }
        }

    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("gap_images", "gap_audio")
    FUNCTION = "extract"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = (
        "Extract only the generated center from a decoded Instrumentality target. "
        "The output contains neither locked source handle, so it can be assembled "
        "between the original paired clips without duplicate frames."
    )

    def extract(self, images, audio, left_context_frames, right_context_frames, frame_rate=24.0):
        left = int(left_context_frames)
        right = int(right_context_frames)
        total = int(images.shape[0])
        if left < 0 or right < 0 or left + right >= total:
            raise ValueError(
                "Instrumentality: cannot extract a non-empty middle from %d frames "
                "with %d left + %d right context frames" % (total, left, right)
            )
        frame_rate = float(frame_rate)
        if frame_rate <= 0:
            raise ValueError("Instrumentality: frame_rate must be positive")
        waveform = audio.get("waveform")
        sample_rate = int(audio.get("sample_rate", 0))
        if getattr(waveform, "ndim", 0) != 3 or sample_rate <= 0:
            raise ValueError("Instrumentality: audio must be a [B,C,S] waveform with sample_rate")
        start = int(round(left / frame_rate * sample_rate))
        end = int(round((total - right) / frame_rate * sample_rate))
        if start >= end:
            raise ValueError("Instrumentality: locked handles consume the decoded audio")
        return (images[left:total - right], {
            "waveform": waveform[..., start:end].contiguous(),
            "sample_rate": sample_rate,
        })
