# Testing

Unit tests that import `meta_binja.core` require Binary Ninja's Python environment because the core module intentionally wraps `binaryninja.RepositoryManager` and `Settings`.

For manual MVP validation in Binary Ninja:

1. Confirm the sidebar loads and lists native extensions.
2. Search for an official/community extension and open its details page.
3. Paste a GitHub plugin repository URL and verify direct navigation.
4. Add a GitHub awesome-list repository under `metaBinja.catalogSources` and refresh.
5. Verify native entries win when a catalog contains the same repository URL.
6. Exercise install/disable/enable/update/uninstall on a disposable Git-backed plugin.
