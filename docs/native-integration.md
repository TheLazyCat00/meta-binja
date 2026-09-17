# Native Extension Manager integration

Meta Binja does not replace Binary Ninja's extension lifecycle internally. `NativeProvider` wraps `RepositoryManager` and `Extension` so official/community plugins continue to use Binary Ninja for installation, dependency handling, update checks, enable/disable state, and uninstall behavior.

This isolation is intentional: if the upstream Extension Manager API changes, the adapter can be updated without rewriting the UI or catalog/Git backends.
