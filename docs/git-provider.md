# Git-backed plugins

For repositories not represented by Binary Ninja's Extension Manager, Meta Binja clones into a dedicated data directory and activates the plugin from Binary Ninja's user plugin directory.

The MVP prefers directory symlinks and falls back to a copy where symlink creation is unavailable. Updates use `git pull --ff-only --recurse-submodules`. Local divergence is intentionally not overwritten automatically.
