# Native Extension Manager integration

Meta Binja does not replace Binary Ninja's extension lifecycle internally. `NativeProvider` wraps `RepositoryManager` and `Extension` so official/community plugins continue to use Binary Ninja for installation, dependency handling, update checks, enable/disable state, and uninstall behavior.

This isolation is intentional: if the upstream Extension Manager API changes, the adapter can be updated without rewriting the UI or catalog/Git backends.


Native install and enable are deliberately restart-aware. After installation Meta Binja re-resolves the extension from `RepositoryManager`, requests enablement, and considers the operation successful when Binary Ninja has persisted `enabled=True` even if the plugin is not live-loaded in the current process. The UI exposes `Extension.running` separately; `enabled=True` with `running=False` is shown as **Not loaded**. Restart Binary Ninja after native lifecycle changes. If the extension remains **Not loaded** after restart, use Binary Ninja's native Extension Manager `@failed_to_load` filter and logs to diagnose the plugin-level import failure.
