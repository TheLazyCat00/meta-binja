# Contributing

Meta Binja is early-stage. Keep discovery and lifecycle ownership separate: Binary Ninja's native/community repositories provide metadata, while every source-backed plugin should use the shared Git lifecycle. Only package-only/prebuilt entries without a clonable repository URL should delegate lifecycle operations to Binary Ninja's Extension Manager.

When adding catalog formats, normalize them to `PluginEntry` rather than teaching the UI about source-specific schemas. Preserve repository-relative plugin subdirectories so monorepo integrations activate the correct package.
