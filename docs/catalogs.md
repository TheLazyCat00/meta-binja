# Catalog sources

Meta Binja treats extra catalogs as discovery sources. Add them in Binary Ninja Settings under `metaBinja.catalogSources`.

Supported MVP inputs:

- A GitHub repository URL. Meta Binja reads its README via the GitHub API.
- A raw Markdown URL containing links to plugin repositories.
- A JSON URL containing either an array or a `plugins` / `entries` array.

Catalogs are cached on disk for six hours; Refresh re-reads them immediately. Set `GITHUB_TOKEN` in your environment to raise GitHub's API rate limit.

If the same repository appears in Binary Ninja's native/community catalog, that entry supplies the presentation metadata. Lifecycle state still comes from Meta Binja's Git checkout, so native/community and direct-repository installs share one implementation.
