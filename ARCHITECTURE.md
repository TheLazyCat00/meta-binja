# Architecture

Meta Binja presents a single plugin-management UI while keeping lifecycle behavior behind providers.

## Native extensions

`NativeProvider` wraps Binary Ninja's public `RepositoryManager` / `Extension` API. Official and community extensions remain owned by Binary Ninja, so install/uninstall, dependency handling, enable/disable, and update semantics stay consistent with the native manager.

## Arbitrary Git repositories

`GitProvider` manages repositories that are not represented by native extensions. Repositories are cloned into `meta-binja/repos` beneath Binary Ninja's user directory and activated from the normal user plugin directory. Directory symlinks are preferred; the MVP falls back to copying when symlink creation is unavailable.

Meta Binja deliberately does not execute arbitrary setup scripts or post-install hooks. Installing a Binary Ninja plugin still means trusting code that Binary Ninja may import and execute.

## Additional catalogs

`CatalogProvider` is discovery-only. The MVP accepts:

- Markdown awesome-list style links.
- JSON arrays of repository URLs.
- JSON objects with a `plugins` or `entries` array.

Entries are normalized to the same `PluginEntry` model. Catalog results are deduplicated against native plugins by canonical repository URL, with the native extension taking precedence.

## Repository metadata

`metadata.py` holds the Qt-free fetching layer: README retrieval, Markdown link
rewriting, GitHub repository facts, and the TTL cache shared by catalogs,
READMEs, and facts. See [docs/metadata.md](docs/metadata.md).

## Threading

The UI owns a small thread pool. Refreshes, catalog downloads, README fetches,
and every lifecycle action run there, and results return to the UI thread
through signals; a stale result is discarded when the user has moved on. The
panel keeps each task alive until it reports back, because a pool-owned
runnable is destroyed with its signal sender. A refresh rebuilds the shared
registry, so one is dropped while another refresh or a lifecycle action is in
flight. Cache updates are a read-modify-write, so they are serialized per cache
path and each write lands through its own temporary file.

## Search behavior

The main search field supports normal token search and direct repository URLs. A URL first resolves against known native/catalog entries; otherwise it opens a Git-backed management page directly. Token search ranks exact and prefix name matches above description matches.

## Next steps

- Windows junction activation rather than copy fallback.
- Dependency preview/install flow for Git plugins.
- First-class signed catalog schema.
- README retrieval through each host's own API rather than conventional paths.
