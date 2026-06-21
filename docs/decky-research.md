# Decky Loader Research Notes

Research date: 2026-06-21

## Sources Checked

- Decky Loader repo: https://github.com/SteamDeckHomebrew/decky-loader
- Decky plugin template: https://github.com/SteamDeckHomebrew/decky-plugin-template
- Getting Started wiki: https://wiki.deckbrew.xyz/en/plugin-dev/getting-started
- Environment Variables wiki: https://wiki.deckbrew.xyz/plugin-dev/env-vars
- Submitting Plugins wiki: https://wiki.deckbrew.xyz/en/plugin-dev/submitting-plugins
- Frontend library docs: https://wiki.deckbrew.xyz/en/api-docs/decky-frontend-lib/README.md

## Plugin Shape

The wiki describes a typical plugin as:

```text
assets/
defaults/
main.py
plugin.json
package.json
README.md
LICENSE
src/
  index.tsx
```

The official template currently uses:

- `@decky/rollup` for the frontend build.
- `@decky/ui` for Steam Deck styled UI components.
- `@decky/api` for frontend/backend calls.
- `plugin.json` for Decky/plugin-store metadata.
- `package.json` for package identity, versioning, and CI/build metadata.

## Backend Calls

The current template exposes Python methods on a `Plugin` class and calls them from TypeScript using `callable` from `@decky/api`. Older docs also show `serverAPI.callPluginMethod`, so the scaffold uses the newer template style.

Backend lifecycle hooks:

- `_main`: called when the plugin loads.
- `_unload`: called when Decky unloads the plugin.
- `_uninstall`: called during uninstall cleanup.
- `_migration`: optional migration hook, useful if settings/runtime paths change later.

## Runtime Paths

Relevant Decky-provided variables:

- `DECKY_USER_HOME`: user home where Decky resides, typically `/home/deck`.
- `DECKY_HOME`: Decky's root, typically `/home/deck/homebrew`.
- `DECKY_PLUGIN_SETTINGS_DIR`: recommended persistent config directory.
- `DECKY_PLUGIN_RUNTIME_DIR`: recommended runtime data directory.
- `DECKY_PLUGIN_LOG_DIR`: recommended persistent log directory.
- `DECKY_PLUGIN_DIR`: installed plugin directory.
- `DECKY_PLUGIN_VERSION`: version from `package.json`.

This plugin should store active move/restore metadata in `DECKY_PLUGIN_SETTINGS_DIR`, because that data is required to recover from unloads and reboots.

## Store Submission Constraints

The Decky store review process requires:

- Public source; no private repositories for store submission.
- A license file.
- No black-box binaries or deliberately obfuscated code.
- A PR to `SteamDeckHomebrew/decky-plugin-database` adding the plugin repo as a submodule under `plugins/`.
- Testing on Steam Deck running SteamOS, with Beta/Preview testing when the plugin touches behavior likely affected by OS changes.

Important risk for this project: the submission page currently says plugins that use LLM-based code are rejected. If store distribution is a goal, this needs a human-authored rewrite/review trail before submission.

## Implications For Decky RAMDisk

- The plugin needs `"root"` in `plugin.json`, because `mount`, `umount`, ownership repair, and some library operations require elevated permissions.
- Root makes the review bar higher, so the code should keep filesystem actions narrow, readable, logged, and easy to audit.
- The plugin should avoid bundled binaries unless absolutely necessary. Native Python plus system commands is preferable.
- The plugin must not silently delete anything. The restore metadata and one-active-game invariant are central safety features.

