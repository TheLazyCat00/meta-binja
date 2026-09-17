# Catalog sources

Meta Binja treats extra catalogs as discovery sources. Add them in Binary Ninja Settings under `metaBinja.catalogSources`.

Supported MVP inputs:

- A GitHub repository URL. Meta Binja reads its README via the GitHub API.
- A raw Markdown URL containing links to plugin repositories.
- A JSON URL containing either an array or a `plugins` / `entries` array.

Catalog entries do not override Binary Ninja-managed extensions. If the same repository appears in a native Extension Manager repository, the native entry wins and all lifecycle operations remain delegated to Binary Ninja.
