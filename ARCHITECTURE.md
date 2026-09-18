# Architecture

Meta Binja presents a single plugin-management UI while keeping lifecycle behavior behind providers.

## Native extensions

`NativeProvider` wraps Binary Ninja's public `RepositoryManager` / `Extension` API. Official and community extensions remain owned by Binary Ninja, so install/uninstall, dependency handling, enable/disable, and update semantics stay consistent with the native manager. Meta Binja records both configured enablement and the current-process `running` state; an enabled native extension that is not running is shown as **Not loaded** instead of being reported as active.

## Arbitrary Git repositories

`GitProvider` manages repositories that are not represented by native extensions. Repository storage and plugin activation intentionally use different identities:

- Checkouts live under `meta-binja/repos` beneath Binary Ninja's user directory with a collision-safe `<canonical-repo>-<hash>` name.
- Enabled plugins are exposed from the normal user `plugins` directory with the repository's case-preserving basename, for example `plugins/RouteNinja`. This matters because the directory is also a Python package name and plugins may use absolute self-imports.
- `managed.json` records both the source URL and the public activation name. Legacy hash-suffixed activations are migrated automatically when they can be moved safely.
- If the desired public plugin name already belongs to something Meta Binja does not manage, enable/install fails rather than changing the package name or overwriting the existing plugin.

Directory symlinks are preferred. If symlink creation is unavailable, Meta Binja copies the checkout and writes a private ownership marker into the copy so later disable/update/uninstall operations only mutate paths it can prove it owns.

Git plugins with a `requirements.txt` are installed through Binary Ninja's own Python dependency installer before activation and again after updates. This keeps interpreter, virtual-environment, proxy, and per-version site-package behavior aligned with Binary Ninja. If dependency installation fails, the checkout is kept for diagnosis/retry but is not left enabled.

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
and lifecycle actions start there, and results return to the UI thread through
signals; a stale result is discarded when the user has moved on. Native
extension activation and deactivation are the exception: Binary Ninja may load
or unload plugin code as part of those calls, so `NativeProvider` marshals them
through `execute_on_main_thread_and_wait` onto Binary Ninja's registered main
thread. Installation/download work stays on the worker to avoid blocking the
UI. After installation the provider re-resolves the Extension from
`RepositoryManager` before enabling it, and it verifies the persisted `enabled`
state rather than requiring a successful live load. This matches Binary Ninja's
documented install -> enable -> restart lifecycle. The panel keeps each task alive until it reports back, because a pool-owned
runnable is destroyed with its signal sender. A refresh rebuilds the shared
registry, so one is dropped while another refresh or a lifecycle action is in
flight. Cache updates are a read-modify-write, so they are serialized per cache
path and each write lands through its own temporary file.

## Search behavior

The main search field supports normal token search and direct repository URLs. A URL first resolves against known native/catalog entries; otherwise it opens a Git-backed management page directly. Token search ranks exact and prefix name matches above description matches.

## Next steps

- Windows junction activation rather than copy fallback.
- Dependency preview/confirmation UI for Git plugins.
- First-class signed catalog schema.
- README retrieval through each host's own API rather than conventional paths.
