# Vanta 0.1.3 fresh-install acceptance

Automated and installed-release evidence uses an empty root below `F:\VantaAcceptance\0.1.3`, never populated AppData. Verify the NSIS installer, packaged-sidecar self-test, console-free launch, actual hardware report, arbitrary-drive storage selection, durable bootstrap, and authoritative installation jobs before engine/model downloads.

For an installed desktop run that must not inspect or mutate a tester's existing Vanta profile, set `VANTA_ACCEPTANCE_DEFAULT_DATA_DIR` and `VANTA_ACCEPTANCE_BOOTSTRAP_DIR` to absolute disposable directories before launching the installed executable. These acceptance-only overrides are ignored when unset; normal releases continue to resolve the standard Windows application-data and local-data folders.

The native portion is tracked in `MANUAL_ACCEPTANCE_CHECKLIST.md`: select neutral storage, install and pause/resume the engine, download only RealVisXL fp16, verify it through real diagnostic generation, generate one user-visible image, exercise Gallery file actions, close/reopen, and confirm managed child processes exit. Do not call this release stable until those checks and both full transfers have completed.
