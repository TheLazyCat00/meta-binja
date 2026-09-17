# Contributing

Meta Binja is early-stage. For changes that affect plugin lifecycle behavior, keep the provider boundary intact: native Binary Ninja extensions should continue to delegate to Binary Ninja's own Extension Manager API, while arbitrary Git repositories should remain isolated in the Git provider.

When adding catalog formats, normalize them to `PluginEntry` rather than teaching the UI about source-specific schemas.
