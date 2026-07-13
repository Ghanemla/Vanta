# Capability evidence

## 2× local upscaling

- Pack: `Finish — RealESRGAN 2×` (`realesrgan_x2plus`).
- User-selected local source is copied into Vanta-owned model storage, with original path, selected license note, exact byte size, and SHA-256 recorded in SQLite.
- Workflow: `image-upscale-realesrgan-v1` uses ComfyUI's `UpscaleModelLoader` and `ImageUpscaleWithModel`. The engine node performs tiled model execution when required by the image dimensions.
- Evidence run on 2026-07-13: source `generation-497627f2dfc547289a38965752ce1bee` at 832×1216 produced derivative `generation-4de5805e9eef4dc5a476fa6efcf268fe` at 1664×2432. The persisted derivative records the source generation, profile, model filename, model SHA-256, scale, duration, and AI disclosure.

## Optional 4× evaluation

- Pack: `Finish — UltraSharp 4× (optional)` (`ultrasharp_x4`), never set as the default profile.
- Evidence run on 2026-07-13: the same 832×1216 source produced derivative `generation-7a1156ab643c4a9998d4963f6d4a8619` at 3328×4864 in 18 seconds with model SHA-256 `a5812231fc936b42af08a5edba784195495d303d5b3248c24489ef0c4021fe01`.
- The optional profile is appropriate for deliberate final exports; its substantially larger output and slower execution preserve RealESRGAN 2× as Vanta’s safer default.

## Local identity conditioning

- Pack: `Identity — Plus Face SDXL` (`identity_plus_face_sdxl`). It requires both the IP-Adapter Plus Face SDXL ViT-H adapter and a compatible CLIP Vision H encoder; neither artifact alone is considered ready.
- The adapter is pinned to `h94/IP-Adapter` revision `018e402774aeeddd60609b4ecdb7e298259dc729`, with downloaded SHA-256 `677ad8860204f7d0bfba12d29e6c31ded9beefdf3e4bbd102518357d31a292c1`. The provided encoder was copied under its required runtime name and verified as SHA-256 `64a7ef761bfccbadbaa3da77366aac4185a6c58fa5de5f589b42a65bcc21f161`.
- Vanta verifies that the adapter, encoder, and the compatible `IPAdapterUnifiedLoader` and `IPAdapterAdvanced` runtime nodes are all available. Generation then compiles `image-sdxl-identity-ipadapter-v1` conditioning from the character’s primary owned reference before sampling.
- Evidence run on 2026-07-13: integrated generation job `job-aafd8e7d1f554781b75d91b4edad1a3d` completed with the real local IP-Adapter workflow and produced `generation-4df337f2a0ef410aba95d1977275e49e`.
