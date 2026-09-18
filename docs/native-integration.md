# Native Extension Manager integration

Binary Ninja's Extension Manager is now primarily a **discovery source** for Meta Binja.

For each native/community catalog entry, Meta Binja reads the extension name, description, author, published version, `project_url`, and optional `subdir`. When `project_url` is a clonable repository URL and the extension declares the `python3` API, all install/update/enable/disable/uninstall operations are delegated to `GitProvider`, exactly like a repository URL pasted into search.

If an older Meta Binja version already installed that entry through Binary Ninja's native manager, the first Git install performs a one-time handoff: the native copy is disabled and uninstalled before the Git checkout is activated. This prevents duplicate plugin packages after restart.

Non-Python, compiled/prebuilt, and package-only extensions retain Binary Ninja's native lifecycle as a fallback.
