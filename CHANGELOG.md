# Changelog

## Unreleased

- Separated Git checkout identity from Binary Ninja plugin-package identity: private checkouts remain hash-suffixed, while enabled plugins use the repository's case-preserving basename (for example `RouteNinja`).
- Added automatic migration from the legacy hash-suffixed activation layout and persisted activation names in `managed.json`.
- Added ownership-aware activation handling and explicit name-collision failures so Meta Binja does not overwrite unrelated plugins or mutate their package names.
- Git plugins now install `requirements.txt` through Binary Ninja's configured Python dependency installer before activation and after updates; dependency failures leave the plugin disabled.

## 0.2.0

- Plugin pages now lead with the repository's README, rendered from Markdown
  with relative links and images resolved, plus star count, license, and last
  push date where the host provides them.
- Results are a sortable table with separate Source and Status columns, color
  coded, instead of a status line beneath each name.
- Added a filter for installed/outdated/not-installed plugins and a result
  count, and ranked search results by name matches.
- Moved Git and network work off the UI thread, and cached catalogs, READMEs,
  and repository facts on disk with a per-kind TTL.
- Added a standalone manager window, repository open/copy actions, keyboard
  shortcuts, an inline status line, and a confirmation before uninstalling.
- Added headless Qt tests and Binary Ninja stubs so the suite runs with
  `python3 -m unittest discover`.

## 0.1.0

Initial MVP:
- Unified native/community extension search.
- Direct Git repository URL management.
- Additional GitHub-repository, Markdown, and JSON catalog sources.
- Install/uninstall, enable/disable, and update controls.
- Native-extension deduplication by repository URL.
