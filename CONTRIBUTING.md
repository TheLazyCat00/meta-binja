# Contributing

Meta Binja is early-stage. Keep discovery and lifecycle ownership separate: Binary Ninja's native/community repositories provide metadata, while source-backed Python 3 entries with a clonable repository URL use the shared Git lifecycle. Compiled, prebuilt, non-Python, and package-only entries use Binary Ninja's Extension Manager lifecycle even when their metadata includes a clonable repository URL.

When adding catalog formats, normalize them to `PluginEntry` rather than teaching the UI about source-specific schemas. Preserve repository-relative plugin subdirectories so monorepo integrations activate the correct package.
