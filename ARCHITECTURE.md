# Architecture

Meta Binja presents a single plugin-management UI while keeping lifecycle behavior behind providers.

## Native/community discovery

`NativeProvider` uses Binary Ninja's public `RepositoryManager` / `Extension` API as a discovery source. For entries with a clonable `project_url`, Meta Binja keeps the native catalog's name, description, version, repository URL, and `subdir`, but lifecycle state comes from `GitProvider`. Existing native-manager installs are disabled/uninstalled once during the handoff so the native package cannot conflict with the Git-managed checkout.

Extensions without a clonable project URL retain Binary Ninja's native lifecycle as a fallback for package-only/prebuilt releases.

## Git lifecycle

`GitProvider` manages both direct repository installs and source-backed native/community catalog entries. Repository storage and plugin activation intentionally use different identities:

- Checkouts live under `meta-binja/repos` beneath Binary Ninja's user directory with a collision-safe `<canonical-repo>-<hash>` name.
- Enabled plugins are exposed from the normal user `plugins` directory with the repository's case-preserving basename, for example `plugins/RouteNinja`. This matters because the directory is also a Python package name and plugins may use absolute self-imports.
- `managed.json` records the source URL, public activation name, and optional catalog-provided plugin subdirectory. Legacy hash-suffixed activations are migrated automatically when they can be moved safely.
- If the desired public plugin name already belongs to something Meta Binja does not manage, enable/install fails rather than changing the package name or overwriting the existing plugin.

Directory symlinks are preferred. If a catalog declares a plugin `subdir`, Meta Binja exposes that subdirectory rather than the repository root. If symlink creation is unavailable, Meta Binja copies only the active plugin package and writes a private ownership marker into the copy so later disable/update/uninstall operations only mutate paths it can prove it owns.

Git plugins with a `requirements.txt` at the repository root and/or active plugin subdirectory are installed through Binary Ninja's own Python dependency installer before activation and again after updates. This keeps interpreter, virtual-environment, proxy, and per-version site-package behavior aligned with Binary Ninja. If dependency installation fails, the checkout is kept for diagnosis/retry but is not left enabled.

Meta Binja deliberately does not execute arbitrary setup scripts or post-install hooks. Installing a Binary Ninja plugin still means trusting code that Binary Ninja may import and execute.

## Additional catalogs

`CatalogProvider` is discovery-only. The MVP accepts:

- Markdown awesome-list style links.
- JSON arrays of repository URLs.
- JSON objects with a `plugins` or `entries` array.

Entries are normalized to the same `PluginEntry` model. Native/community metadata takes presentation precedence for matching repository URLs, while installed/enabled/update state is overlaid from the single Git lifecycle.

## Repository metadata

`metadata.py` holds the Qt-free fetching layer: README retrieval, Markdown link
rewriting, GitHub repository facts, and the TTL cache shared by catalogs,
READMEs, and facts. See [docs/metadata.md](docs/metadata.md).

## Threading

The UI owns a small thread pool. Refreshes, catalog downloads, README fetches,
Git lifecycle actions, and native fallback installation work start there, and
results return to the UI thread through signals; a stale result is discarded
when the user has moved on. Native fallback activation/deactivation and
one-time migration cleanup are marshalled through
`execute_on_main_thread_and_wait` when they can load or unload plugin code.
The panel keeps each task alive until it reports back, because a pool-owned
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
