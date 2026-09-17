# Development notes

The MVP is intentionally structured around provider boundaries. UI code should operate on normalized `PluginEntry` objects rather than source-specific APIs.

For native extensions, use Binary Ninja's Extension Manager APIs. For arbitrary repositories, use the Git provider. For third-party lists, add parsing in the catalog provider and normalize results before they reach the UI.

The repository currently has lightweight URL-focused tests. Integration testing requires a Binary Ninja installation because `binaryninja` and `binaryninjaui` are provided by the application.
