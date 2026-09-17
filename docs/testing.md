# Testing

Run the suite from the repository root:

```sh
python3 -m unittest discover
```

`tests/stubs.py` provides the `binaryninja` and `binaryninjaui` modules that
Binary Ninja itself would supply, so the provider, catalog, metadata, and URL
tests run anywhere.

`tests/test_ui.py` builds the real Qt panel against those stubs using Qt's
offscreen platform, covering table rendering, filtering, search ranking,
navigation, sorting, install, and both outcomes of the uninstall confirmation.
It skips when PySide6 is not installed.

For manual validation inside Binary Ninja:

1. Confirm the sidebar loads and lists native extensions with source and status
   in separate columns.
2. Open a plugin and confirm its README renders with working links and images.
3. Paste a GitHub plugin repository URL and verify direct navigation.
4. Add a GitHub awesome-list repository under `metaBinja.catalogSources` and
   refresh.
5. Verify native entries win when a catalog contains the same repository URL.
6. Exercise install/disable/enable/update/uninstall on a disposable Git-backed
   plugin, and confirm the UI stays responsive throughout.
