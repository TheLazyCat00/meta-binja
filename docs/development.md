# Development notes

The MVP is intentionally structured around provider boundaries. UI code should operate on normalized `PluginEntry` objects rather than source-specific APIs.

For native extensions, use Binary Ninja's Extension Manager APIs. For arbitrary repositories, use the Git provider. For third-party lists, add parsing in the catalog provider and normalize results before they reach the UI.

Keep fetching and parsing in `metadata.py`, which imports neither Qt nor Binary Ninja, so it stays testable outside the application. Anything the UI does off the main thread goes through the panel's task runner rather than a bare `QRunnable`.

`tests/stubs.py` substitutes the `binaryninja` and `binaryninjaui` modules, so the whole suite — including the Qt panel, rendered offscreen — runs with `python3 -m unittest discover`. Integration testing against real extensions still requires a Binary Ninja installation.
