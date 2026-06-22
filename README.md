# Decky RAMDisk

Decky RAMDisk is an early-stage Decky Loader plugin for temporarily staging a selected Steam game on a RAM-backed disk.

The intended flow:

1. Scan installed Steam games.
2. Show only games whose install size is smaller than roughly 50% of total RAM.
3. Let the user select one eligible game.
4. Create a temporary RAM disk and initialize it as a Steam library location.
5. Move or stage the selected game onto the RAM disk.
6. Persist enough state to restore the game to its original library with one button.

This repository is currently a scaffold and design spike. The backend includes parsers and real move/restore operations, but destructive filesystem behavior needs more Steam Deck testing before any store submission.

The staging path uses **overlayfs** on top of a tmpfs so the original install
is never deleted or modified by the plugin itself. Revert offers two
modes: **discard** (fast, throws away in-game writes) and **commit**
(rsyncs writes back before unmounting). Plugin state is persisted with
phase tracking and atomic writes, and a recovery pass on startup heals
stale state from reboots or crashed operations.

Real move/restore operations are enabled by default. Set `DECKY_RAMDISK_ENABLE_MUTATIONS=0` for development sessions where the UI should only allow dry-run previews.

## Current Structure

```text
assets/                    Static assets for Decky packaging
defaults/                  Future packaged helper/config files
docs/                      Architecture and Decky research notes
main.py                    Decky Python backend entry point
plugin.json                Decky plugin metadata
package.json               Frontend package metadata and build scripts
py_modules/ramdisk_plugin/ Python backend modules
src/index.tsx              Decky React panel
```

## Testing

Unit tests cover the state store, stage/revert flows, and recovery
passes through a faked `OperationRunner` so they do not require root:

```bash
python3 -m unittest discover -s tests -v
```

## Development

Decky plugin development currently follows the official template:

```bash
pnpm i
pnpm run build
```

The template expects Node.js 16.14+ and pnpm 9. Decky uses a Rollup build through `@decky/rollup`, with frontend APIs from `@decky/api` and UI components from `@decky/ui`.

## Safety Status

The plugin is marked with Decky's `root` flag because mounting `tmpfs`, moving Steam libraries, and restoring game directories require elevated filesystem access. That root flag should stay paired with:

- A preview/dry-run operation before real moves.
- A single active RAM-disk move at a time.
- Persistent restore metadata in `DECKY_PLUGIN_SETTINGS_DIR`.
- Defensive path checks before any remove, move, symlink, or unmount operation.
- Steam Deck testing on real SteamOS before users touch it.

## Useful Links

- Decky Loader: https://github.com/SteamDeckHomebrew/decky-loader
- Decky plugin template: https://github.com/SteamDeckHomebrew/decky-plugin-template
- Decky plugin getting started: https://wiki.deckbrew.xyz/en/plugin-dev/getting-started
- Decky plugin environment variables: https://wiki.deckbrew.xyz/plugin-dev/env-vars
- Decky plugin submissions: https://wiki.deckbrew.xyz/en/plugin-dev/submitting-plugins
- Decky frontend library: https://github.com/SteamDeckHomebrew/decky-frontend-lib
