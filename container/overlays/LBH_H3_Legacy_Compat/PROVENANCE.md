# LBH MiniMax H3 legacy compatibility nodes

These files restore the exact workflow node IDs:

- `H3LatentUpscalerNode3DV3`
- `MinimaxH3LatentUpscalerNode3D`

The three implementation files under `nodes/` are extracted unchanged from
commit `8b5058a` of:

`https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git`

The package registration files intentionally expose only those two historical
IDs, avoiding conflicts with the current LBH package installed alongside it.
