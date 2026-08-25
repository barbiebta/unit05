# Dummyplug bundler: implementation handoff

Dummyplug remains a client-side authoring and packaging application. `unit05` is the separate server-side executor. Dummyplug does not manage ComfyUI, a render queue, server status, result delivery, or server credentials.

## Deliverable

Extend the existing single-shot workflow so **Seal job ZIP** produces one self-contained archive that `unit05` can run without access to Dummyplug or the original source files.

Do not redesign the existing asset library, sidecars, prompt generator, or six-section H3 validation.

## Add these job controls

- Output width and height, with useful portrait and landscape presets.
- Duration in seconds.
- FPS.
- Seed plus a randomize-before-sealing action. The ZIP always contains the resolved integer seed.
- Steps.
- Sampler.
- `shift_video` and `shift_audio`.
- Reference image size (`match` or `max`).
- Input scaling: `Auto` (DaSiWa's native short-edge 2048 behavior), `Off`, `Target`, `Fit`, `Fill and crop`, `Fit and pad`, or `Long side with divisible crop`.
- Output container, codec, quality, audio codec, and audio bitrate.
- Workflow preset, initially `dasiwa-ref2va:v1`.

## Add per-reference controls

- Explicit attachment order. This order remains the authority for `<Picture N>`, `<Video N>`, and `<Audio N>` numbering.
- Include/exclude for the current job without removing the asset from the library.
- For every video: `trim_start` and `trim_end` in seconds. Default to the whole source video.
- Validate that `0 <= trim_start < trim_end <= source_duration` when source duration is known.
- Record source width, height, duration, MIME type, byte size, and SHA-256 when sealing.

The original video goes into the ZIP. Trimming is declarative metadata; Dummyplug does not create a trimmed derivative.

## ZIP contract

Use the existing ZIP export mechanism. Media is included as-is. The archive contains:

```text
manifest.json
prompt.txt
request.txt
reference-map.json
checksums.json
assets/
metadata/
```

`checksums.json` maps every bundled relative path except itself to its SHA-256.

### `manifest.json`

```json
{
  "schema": "dummyplug.h3-job.v1",
  "job_id": "UUID",
  "created_at": "ISO-8601 timestamp",
  "title": "optional human title",
  "workflow": {
    "template": "dasiwa-ref2va",
    "version": 1
  },
  "generation": {
    "width": 768,
    "height": 1344,
    "duration": 5,
    "fps": 24,
    "seed": 616271471367168,
    "steps": 25,
    "sampler": "res_multistep",
    "shift_video": 12,
    "shift_audio": 3,
    "ref_image_size": "match",
    "input_scaling": "Auto",
    "output": {
      "container": "Auto",
      "codec": "Auto",
      "quality": 21,
      "audio_codec": "Auto",
      "audio_bitrate": "192k"
    }
  },
  "references": [
    {
      "asset_id": "stable Dummyplug asset ID",
      "path": "assets/video-1__performance.mov",
      "kind": "video",
      "order": 0,
      "label": "<Video 1>",
      "sha256": "hex digest",
      "size": 12345678,
      "mime_type": "video/quicktime",
      "source_width": 768,
      "source_height": 1344,
      "source_duration": 11.083333,
      "trim_start": 0,
      "trim_end": 5
    }
  ]
}
```

Images omit video-only fields. `path` is always a safe relative POSIX path inside the ZIP. No local absolute path or credential may enter the archive.

## Sealing behavior

1. Validate the six-section prompt and selected-reference limits.
2. Resolve the final seed.
3. Validate generation settings and video trims.
4. Snapshot reference order and H3 labels.
5. Hash the included files.
6. Write the manifest, prompt, request, reference map, sidecars, assets, and checksums.
7. Download the finished archive as `<job_id>.dummyjob.zip`.

The sealed ZIP is immutable. Later edits in Dummyplug create a new job UUID and a new ZIP.

## UI boundary

Add the new controls to the existing prompt/reference surface and keep ZIP export as the final action. An optional **Send sealed ZIP** convenience may SFTP the already-created archive to `unit05`'s `inputs` folder: upload as `.partial`, then rename to `.dummyjob.zip` only after the upload finishes. Keep its connection settings outside the ZIP. This is transport only; do not add queue control, render progress, Comfy management, or output-download UI to Dummyplug.

## Tests

- Manifest schema and required fields.
- Fixed and randomized seeds are resolved before sealing.
- Attachment order and H3 labels agree.
- Per-video trim validation.
- ZIP contains every manifest reference.
- Bundled SHA-256 values match the bytes in the ZIP.
- No absolute source paths or secrets appear in the archive.
- Existing sidecar and six-section prompt tests continue to pass.

## Acceptance example

Seal one 768x1344, 5-second, 24-FPS REF2VA job with two images and one trimmed video. Extract the ZIP independently and confirm that its prompt, manifest, references, settings, hashes, and sidecars are sufficient to submit the job without reopening Dummyplug.
