# Vanta manual acceptance checklist

Last updated: 2026-07-13

These checks need native Windows interaction and must be run against the final NSIS installer. They are deliberately not represented as automated or simulated evidence.

## Phase 0 installed-release checks

- [ ] Install the current NSIS release on a clean user profile; no terminal window appears.
- [ ] Launch Vanta from Start. Confirm the dark title bar supports drag, minimize, maximize/restore, close, keyboard navigation and screen-reader labels.
- [ ] Complete first-run setup: inspect hardware, install/repair the managed local image engine, import a user-owned SDXL `.safetensors` model, and wait for diagnostic verification.
- [ ] Create an original adult character and generate an image through the Create screen.
- [ ] Cancel an active generation and confirm its truthful cancelled state.
- [ ] Open the Gallery image, use Generate Similar, and confirm both images and metadata persist after a normal restart.
- [ ] Close normally and confirm Vanta-owned sidecar/engine processes stop. Repeat after a forced desktop-process close and confirm the Job Object cleanup stops children.
- [ ] Reinstall or upgrade without deleting `%LOCALAPPDATA%\\Vanta`; confirm database, model records, gallery media, presets and characters remain.

## Later feature acceptance

Add a checked, dated evidence line here only after the matching capability creates a real local artifact through the Vanta UI. Never substitute fixture output or a direct engine invocation for this checklist.

- [ ] Create an original adult character, add owned local image references, import a compatible SDXL LoRA, assign it to the character, then generate through Create. Confirm the Gallery metadata names the LoRA.
- [ ] From a Gallery image, choose Create variation, set a different seed or prompt direction, and confirm the derived image retains its source-generation metadata after restart.
- [ ] Exercise each controlled variation goal (composition, identity, pose, clothing, background, lighting, expression and custom), then restart and confirm its derivative source, goal and denoise metadata remain visible.
- [ ] Open a full-resolution Gallery image in Inpaint, test zoom/pan/fit, brush/eraser/size/clear/invert, cancel one run, complete one run, inspect before/after and mask metadata, then restart and confirm both original and derivative remain available.
- [ ] Identity Lock, Pose Control, Inpainting, Upscaling, Image-to-Video, Reference Motion, LoRA Training and Captioning show Ready only when their reviewed manifests and real node/model health checks pass. Exercise Verify, Repair and Remove, and confirm training user data survives component repair/removal.
- [ ] Import the preserved self-contained FLUX checkpoint into Realistic — Maximum, select it in Create, confirm 768 × 1024 / 20 steps / guidance 3.5 defaults, generate an original image, and confirm Gallery records `image-flux-photoreal-v1`, model hash, seed and disclosure metadata. Switch back to Balanced and confirm pose/editing controls become available again.
- [ ] From an image Gallery item, choose Animate image, render each 2/3/4-second option, cancel one active render, and confirm successful MP4 playback plus source, seed, profile, frame count, model hashes and disclosure metadata after restart.
- [ ] Import an owned motion clip by file picker and drag/drop, confirm rights, test trim/crop/fit/smoothing/strength and re-extraction, play the face-disabled skeleton preview, then use it in Animate image. Confirm the resulting Gallery record names the motion asset and explicitly reports no identity, audio or branding transfer.
- [ ] Create a LoRA dataset for an original character, import owned images, review duplicate/quality warnings and local captions, edit a caption, compare Safe 12 GB and Balanced 12 GB estimates, cancel and resume a run from a saved epoch, inspect validation samples, select a checkpoint, install it into the character and generate. Confirm Gallery records the trained LoRA hash and trigger token after restart.
- [ ] Exercise Simple and Studio Create modes. Save a complete recipe containing custom positive/negative text, all preset categories, model, LoRA weights, identity, pose, variation, video and sampler settings; restart, reload it and confirm the same controls are restored.
- [ ] In Presets/Recipes, create character/project/global records, edit, duplicate, favorite, search and tag them, export/import JSON, delete user records and restore built-ins. Confirm editing a built-in creates a user-owned copy.
- [ ] In Models & Engine, exercise every action that is applicable to the installed components and packs: Verify, Repair, Update, Pause/Resume, Restart, Remove and Open logs. Confirm errors are human-readable, diagnostics show current processes/ports/resources/provenance, and the exported ZIP contains sanitized local diagnostics.
- [ ] Verify Settings folder buttons, About information, keyboard-only navigation, visible focus, responsive narrow-window layouts, loading/empty/success/failure states, and that no normal workflow opens a terminal or exposes ComfyUI node details.
