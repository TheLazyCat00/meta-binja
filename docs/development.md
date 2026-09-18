# Development notes

The MVP is intentionally structured around provider boundaries. UI code should operate on normalized `PluginEntry` objects rather than source-specific APIs.

Use Binary Ninja's Extension Manager APIs for native/community discovery and package-only fallback. Source-backed entries, whether discovered natively or entered directly, must use `GitProvider` for lifecycle operations. For third-party lists, add parsing in the catalog provider and normalize results before they reach the UI.

Keep fetching and parsing in `metadata.py`, which imports neither Qt nor Binary Ninja, so it stays testable outside the application. Anything the UI does off the main thread goes through the panel's task runner rather than a bare `QRunnable`.

`tests/stubs.py` substitutes the `binaryninja` and `binaryninjaui` modules, so the whole suite — including the Qt panel, rendered offscreen — runs with `python3 -m unittest discover -s tests`. Integration testing against real extensions still requires a Binary Ninja installation.
