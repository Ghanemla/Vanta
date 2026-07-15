# Vanta 0.1.2 fresh-install acceptance

Automated evidence must use an empty data root below `F:\VantaAcceptance`, never populated AppData. Verify the NSIS installer exists, the packaged sidecar self-test succeeds, Vanta launches without a console, the first-run wizard reports actual hardware, and selecting `F:\VantaData` occurs before engine/model downloads.

The native/manual portion is tracked in `MANUAL_ACCEPTANCE_CHECKLIST.md`: choose F: storage, install and cancel/resume the engine, download only RealVisXL fp16, verify it, generate one real image, exercise Gallery file actions, close/reopen, and confirm managed child processes exit. Do not call this release stable until those native checks and the full model transfer have completed.
