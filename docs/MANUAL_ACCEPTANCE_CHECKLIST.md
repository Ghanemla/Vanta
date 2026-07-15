# Vanta manual acceptance checklist

Last updated: 2026-07-15

These checks need native Windows interaction and must be run against the final NSIS installer. They are deliberately not represented as automated or simulated evidence.

## Phase 0 installed-release checks

- [ ] Install the current NSIS release on a clean user profile; no terminal window appears.
- [ ] Launch Vanta from Start. Confirm the dark title bar supports drag, minimize, maximize/restore, close, keyboard navigation and screen-reader labels.
- [ ] Complete first-run setup: inspect hardware, install/repair the managed local image engine, import a user-owned SDXL `.safetensors` model, and wait for diagnostic verification.
- [ ] Create an original adult character and generate an image through the Create screen.
- [ ] Cancel an active generation and confirm its truthful cancelled state.
- [ ] Open the Gallery image, use Generate Similar, and confirm both images and metadata persist after a normal restart.
- [ ] Close normally and confirm Vanta-owned sidecar/engine processes stop. Repeat after a forced desktop-process close and confirm the Job Object cleanup stops children.
- [ ] Reinstall or upgrade without deleting `%APPDATA%\studio.vanta.desktop`; confirm database, model records, gallery media, presets and characters remain.

## Vanta 0.1.3 fresh-install acceptance

- [ ] Install `Vanta_0.1.3_x64-setup.exe` into `F:\VantaAcceptance\0.1.3\AppInstall` and launch it without a console window.
- [ ] In first-run setup, verify real GPU, VRAM, RAM and free-storage values, then choose `F:\VantaAcceptance\0.1.3\StudioData`. Also prove C: is permitted when it has enough space.
- [ ] Start Local Image Engine and confirm the same durable job appears in Models & Engine with stage, bytes, percentage, speed, ETA, destination, Pause, Resume and Cancel.
- [ ] Pause and confirm meaningful file growth stops; resume with a Range request; cancel a separate attempt and confirm it remains Cancelled and never becomes Ready.
- [ ] Download only `RealVisXL_V5.0_fp16.safetensors`, verify the hash and diagnostic workflow, then generate one image and confirm Gallery file actions work after restart.

## V1.0.1 installed media and progress repair

## V1.0.1 storage relocation and managed exports

- [ ] In Settings, confirm the displayed studio-data root and storage usage match the active installation.
- [ ] Select an empty local destination such as `F:\VantaData`, start **Move existing studio data**, observe copy counts/bytes, then wait for the managed-service health check.
- [ ] Confirm the original root remains intact after success and the new root remains selected after restart and Repair Installation.
- [ ] Confirm Gallery images/videos, characters, poses, motion, datasets, LoRAs, presets, recipes, training history, SDXL, FLUX, and video all resolve from the selected root.
- [ ] Start a disposable move, choose **Cancel before switch**, and confirm the original root stays active.
- [ ] If a legacy AppData junction exists, confirm Settings shows and can adopt its resolved target without copying or deleting it.
- [ ] From an image and video Gallery detail, verify **Open file**, **Show in folder**, **Save a copy**, and **Copy file path**. Confirm the saved copy remains usable after Vanta closes and the managed original remains intact.

## V1.0.1 installed media and progress repair

- [ ] Upgrade the existing installation without deleting `%APPDATA%\studio.vanta.desktop`; confirm its database, models, media, training state, recipes and settings are unchanged.
- [ ] Confirm existing Gallery thumbnails, full images, video posters and MP4 playback render without filenames, broken-image icons or alt-text fallback.
- [ ] Confirm existing Pose source/control previews, character references, training dataset thumbnails and checkpoint validation samples render.
- [ ] Restart the orchestrator from Vanta and confirm visible media recovers on the new dynamic loopback port without placing a token in a URL.
- [ ] Run Settings → Repair media library. Confirm valid originals remain byte-for-byte present and missing derivatives are restored or explicitly reported.
- [ ] Start SDXL and FLUX jobs. Confirm Create immediately shows real stage, determinate sampling progress, step count, elapsed time, conservative ETA, queue position, model, family and output dimensions.
- [ ] Navigate away and back during a job, cancel one job, complete another, open its result, dismiss the completed panel, then restart Vanta and confirm an undismissed tracked job is restored.
- [ ] Repeat progress checks for variation or inpainting, upscaling and video. Confirm non-sampling stages are indeterminate rather than displaying invented percentages.
- [ ] Confirm failed training cards show category, explanation and recovery—not a traceback—and that sanitized technical details, retry, saved-state resume and diagnostics remain available.
- [ ] Close Vanta normally and after a forced desktop close; confirm no terminal window appeared and all Vanta-owned child processes stop.

## Later feature acceptance

Add a checked, dated evidence line here only after the matching capability creates a real local artifact through the Vanta UI. Never substitute fixture output or a direct engine invocation for this checklist.

- [ ] Create an original adult character, add owned local image references, import a compatible SDXL LoRA, assign it to the character, then generate through Create. Confirm the Gallery metadata names the LoRA.
- [ ] From a Gallery image, choose Create variation, set a different seed or prompt direction, and confirm the derived image retains its source-generation metadata after restart.
- [ ] Exercise each controlled variation goal (composition, identity, pose, clothing, background, lighting, expression and custom), then restart and confirm its derivative source, goal and denoise metadata remain visible.
- [ ] Open a full-resolution Gallery image in Inpaint, test zoom/pan/fit, brush/eraser/size/clear/invert, cancel one run, complete one run, inspect before/after and mask metadata, then restart and confirm both original and derivative remain available.
- [ ] Identity Lock, Pose Control, Inpainting, Upscaling, Image-to-Video, Reference Motion, LoRA Training and Captioning show Ready only when their reviewed manifests and real node/model health checks pass. Exercise Verify, Repair and Remove, and confirm training user data survives component repair/removal.
- [ ] Import the preserved self-contained FLUX checkpoint into Realistic — Maximum, select it in Create, confirm 768 × 1024 / 20 steps / guidance 3.5 defaults, generate an original image, and confirm Gallery records `image-flux-photoreal-v1`, model hash, seed and disclosure metadata. Switch back to Balanced and confirm pose/editing controls become available again.
- [ ] From an image Gallery item, choose Animate image. Render Safe (2 seconds) and Standard (4 seconds), cancel one active render, and confirm successful MP4 playback plus source, seed, duration profile, frame count, model hashes and disclosure metadata after restart. Confirm Extended (6–8 seconds) remains disabled until this hardware has a recorded safe verification.
- [ ] Create a video sequence from an image and extend an existing generated clip. Render multiple safe segments with separate motion prompts, use both a final frame and a selected continuation frame, reorder and remove segments, join the selected order, then restart and confirm segment/final metadata and playback persist.
- [ ] Import an owned motion source up to two minutes by file picker and drag/drop, confirm rights, select a four-second-or-shorter range, test crop/fit/smoothing/strength and re-extraction, play the face-disabled skeleton preview, then use it in Animate image. Confirm the resulting Gallery record names the motion asset and explicitly reports no identity, audio or branding transfer.
- [ ] Create a LoRA dataset for an original character, import owned images, review duplicate/quality warnings and local captions, edit a caption, compare Safe 12 GB and Balanced 12 GB estimates, cancel and resume a run from a saved epoch, inspect validation samples, select a checkpoint, install it into the character and generate. Confirm Gallery records the trained LoRA hash and trigger token after restart.
- [ ] Exercise Simple and Studio Create modes. Save a complete recipe containing custom positive/negative text, all preset categories, model, LoRA weights, identity, pose, variation, video and sampler settings; restart, reload it and confirm the same controls are restored.
- [ ] In Presets/Recipes, create character/project/global records, edit, duplicate, favorite, search and tag them, export/import JSON, delete user records and restore built-ins. Confirm editing a built-in creates a user-owned copy.
- [ ] In Models & Engine, exercise every action that is applicable to the installed components and packs: Verify, Repair, Update, Pause/Resume, Restart, Remove and Open logs. Confirm errors are human-readable, diagnostics show current processes/ports/resources/provenance, and the exported ZIP contains sanitized local diagnostics.
- [ ] Verify Settings folder buttons, About information, keyboard-only navigation, visible focus, responsive narrow-window layouts, loading/empty/success/failure states, and that no normal workflow opens a terminal or exposes ComfyUI node details.
