# Capability evidence

## Complete local recipe and engine management

- Presets cover identity modifiers, wardrobe, expression, pose, location, lighting, camera, quality, negative direction and motion. Built-ins remain immutable/restorable; edits create user-owned copies, while user entries support global/character/project scopes, tags, favorites, search, duplication, deletion and portable JSON.
- Recipes persist the selected character, positive/negative direction, ordered presets, verified model profile/file, weighted LoRA stack, identity, pose, variation, video/reference-motion and Studio generation controls. Normal UI and exported data never expose ComfyUI node IDs.
- Evidence on 2026-07-13: `recipe-0c901773225947c8abd3de25f5618015` round-tripped all ten preset categories plus the real trained LoRA, owned identity reference, extracted pose and reference-motion asset in the existing local SQLite database.
- Models & Engine reports local hardware/resources, loopback ports and processes, active jobs, component/model provenance and sanitized logs. Applicable component and pack controls include install, verify, repair/update, pause/resume, restart and remove; owned LoRAs retain exact hashes and repair from their retained original when available.

## Managed local LoRA training

- Vanta manages pinned `sd-scripts` v0.10.5, isolated Python dependencies, two offline SDXL tokenizer sets and the WD-SwinV2 v3 ONNX captioner. Ready requires CUDA imports, local tokenizer loading and a real ONNX session; training runs force network libraries offline.
- Dataset `dataset-a2c04930e00243028494fa0b580b6c08` contains three owned synthetic Vanta images with local captions, exact hashes, quality metrics, one-subject counts and near-duplicate warnings.
- Evidence on 2026-07-13: Safe 12 GB run `training-run-75c5fa580bde4e75b0437c55d452a350` completed 12/12 steps in 88 seconds and produced checkpoint SHA-256 `afb044d73ad6991a5d2e31a710b05625e043da147c54d56a4b86fb3c794de428`, resumable state and a validation sample.
- Installed LoRA `lora-c618cb1c3a9d44e6b776a47c437c0845` then produced Gallery image `generation-29d6980da0244ed08937f10d8f3bbd2c`; reproducibility metadata records the LoRA ID, filename, hash, weights and AI disclosure.

## Native local image-to-video

- Pack: `Motion — LTXV 2B Safe` (`video_ltx_2b`), pinned to the LTXV 2B distilled FP8 checkpoint and separate T5 XXL FP8 encoder with exact revisions, byte sizes and SHA-256 values.
- Workflow `video-ltxv-i2v-v1` uses native `LTXVConditioning`, `LTXVImgToVideo`, `ManualSigmas`, `SamplerCustomAdvanced` and local H.264 MP4 encoding. Normal users never see ComfyUI nodes.
- Evidence on 2026-07-13: job `job-71bf2fbb460c4f4284cc6bdb290397d7` produced Gallery video `generation-c8090eca693848409d33b5e12b73372b`, a playable 49-frame, 24 fps, 2.04-second MP4 from the accepted local FLUX portrait in 35.34 seconds.

## Identity-safe Reference Motion

- Motion asset `motion-89a989a461624f62a14b24aee54d4538` was imported from Vanta's owned synthetic MP4. Local extraction sampled 16 frames at 8 fps, disabled face landmarks, applied temporal smoothing and persisted a playable pose preview.
- The transfer policy excludes reference identity, face, voice, name, branding and watermarks. The generation graph consumes only Vanta's broad movement description; metadata records all exclusion flags.
- Evidence on 2026-07-13: job `job-1e0be3d9d98b41acb197aaffdb57ece0` produced `generation-ee3f72d06504438da0cff49114d903a3`, a playable 49-frame, 2.04-second MP4 with workflow `video-ltxv-reference-motion-v1` and complete source/motion/model/encoder provenance.

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
