# Implementation Roadmap

## Phase 1: Scaffold

- Create Decky-compatible package structure.
- Add plugin metadata with root flag.
- Add React panel with scan, select, stage, and revert controls.
- Add Python backend modules for memory, Steam scanning, state, and operation planning.

## Phase 2: Safe Discovery

- Improve Steam library discovery against real Steam Deck paths.
- Unit-test VDF parsing with real `libraryfolders.vdf` and `appmanifest_*.acf` samples.
- Include hidden edge cases: missing `SizeOnDisk`, moved libraries, SD-card libraries, special characters in install dirs.
- Add current available RAM filtering and headroom controls.

## Phase 3: Non-Destructive Staging

- Mount `tmpfs` under `/run/media/decky-ramdisk`.
- Copy selected game into the RAM disk.
- Verify size and file count.
- Preserve original install folder until verification passes.
- Decide and implement Steam library registration strategy.

## Phase 4: Switch And Restore

- Atomically redirect Steam to the RAM copy.
- Persist active state before switching.
- Add revert operation with copy-back verification.
- Add stale-state recovery for reboot or failed unmount.

## Phase 5: Decky Review Readiness

- Remove debug flag for release builds.
- Complete license and author metadata.
- Add screenshots and store PNG.
- Document exact root operations.
- Run manual testing on Steam Deck SteamOS.
- Prepare public GitHub repository and plugin-database PR.

