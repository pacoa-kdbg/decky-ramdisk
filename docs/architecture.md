# Architecture

## Product Goal

Decky RAMDisk should make it easy to temporarily run a small installed Steam game from RAM, then restore it to its original Steam library.

## User Flow

1. User opens the plugin panel.
2. Backend reads `/proc/meminfo` and computes an eligibility limit of 50% of total RAM.
3. Backend scans Steam library folders and parses app manifests.
4. Frontend lists games at or below the limit.
5. User selects a game and previews the move.
6. User confirms.
7. Backend creates a RAM-backed mount, creates a Steam library structure, stages the game, and records restore metadata.
8. User later presses Revert.
9. Backend restores the game to the original install location and clears recorded state.

## Backend Modules

- `memory.py`: reads `/proc/meminfo` and returns total, available, and max eligible game size.
- `vdf.py`: small parser for Steam VDF-style app manifests and library metadata.
- `steam.py`: finds Steam roots/libraries and emits installed game records.
- `state.py`: stores one active move in JSON under `DECKY_PLUGIN_SETTINGS_DIR`.
- `operations.py`: plans and executes stage/revert operations.

## Data Model

`SteamGame`:

- `appid`
- `name`
- `install_dir`
- `library_path`
- `size_on_disk`

`ActiveMove`:

- `appid`
- `name`
- `original_library_path`
- `original_install_dir`
- `ramdisk_library_path`
- `ramdisk_mount_path`
- `size_on_disk`

## First Technical Decisions

- Support one active RAM-disk game at a time.
- Use `tmpfs` mounted at `/run/media/decky-ramdisk` as a container for
  the overlayfs lower/upper/work directories.
- Stage with **overlayfs** layered on top of the original install path so
  the original directory is never deleted or modified by the plugin.
- Use `/run` rather than `/home/deck` so the mount is clearly runtime-scoped.
- Persist restore metadata atomically (tmp + fsync + rename) with explicit
  phase tracking (`staging` -> `active` -> `reverting`).
- Require preview/dry-run support from the first implementation.
- Run a recovery pass on plugin load to heal stale state after a reboot
  or a crashed stage/revert.

## Open Design Questions

- Should Steam be asked to add the RAM disk as a library folder through UI/API behavior, or should `libraryfolders.vdf` be edited while Steam is stopped?
- How should shader caches and compatdata be handled for Proton games?
- What amount of RAM headroom should be reserved beyond the game's `SizeOnDisk`?
- Should the plugin reject games with workshop/mod content by default?

## Resolved Design Decisions

- **Move vs symlink:** the plugin does neither. It overlays a tmpfs on
  top of the original install path. The original is preserved as the
  lower layer's source of truth; writes during play land on the tmpfs
  upper layer. Revert chooses whether to keep them.
- **Reboot recovery:** because the original install is never destroyed,
  startup recovery can clear stale state without any data restoration.
  The recovery pass is in `operations.recover` and runs from
  `Plugin._main`.

## Safety Requirements Before Real Use

- Validate all source and target paths before any destructive operation.
- Detect whether Steam is running and avoid editing Steam metadata while it can race the plugin.
- Confirm enough currently available RAM, not only total RAM.
- Write restore metadata before removing or redirecting the original install directory.
- Use copy-then-verify-then-switch semantics.
- Add a recovery screen for stale active state.
- Test on SteamOS stable and current beta/preview if store submission is planned.

