# Meta Binja

A replacement-style plugin manager for Binary Ninja.

Meta Binja brings Binary Ninja's official/community extensions, arbitrary Git repositories, and third-party plugin lists into one searchable interface.

## Features

- Unified, sortable plugin table: name, version, source, and lifecycle status in
  separate columns.
- Plugin pages lead with the repository's README, rendered from Markdown with
  working links and images.
- Filter by installed, outdated, or not-installed plugins, and rank search
  results by name matches.
- Git and network work runs off the UI thread, with catalogs, READMEs, and
  repository facts cached on disk.
- Unified search across Binary Ninja's configured Extension Manager repositories.
- Paste a Git repository URL directly into search to open its management page.
- Install, uninstall, enable, disable, and update official/community extensions using Binary Ninja's own `RepositoryManager` / `Extension` APIs.
- Clone, update, enable/disable, and uninstall arbitrary Git-backed Python plugins.
- Add extra discovery catalogs via `metaBinja.catalogSources` in Binary Ninja Settings.
- Accept a GitHub repository URL for an awesome-list directly, plus raw Markdown and simple JSON catalogs.
- Deduplicate catalog results against native extensions by canonical repository URL.
- Never automatically run arbitrary setup/install scripts from third-party repositories.

## Installation

Clone this repository into Binary Ninja's user plugin directory, then restart
Binary Ninja and open the **Meta Binja** sidebar. For more room, open
**Plugins → Meta Binja → Open Plugin Manager** for a standalone window.

Keyboard: `Ctrl+F` focuses search, `Esc` leaves a plugin page, `F5` refreshes.

## Additional plugin catalogs

Open Binary Ninja Settings and search for **Meta Binja**. Add URLs under:

`metaBinja.catalogSources`

For a GitHub-hosted awesome list, add the repository URL itself:

```text
https://github.com/example/awesome-binja-plugins
```

Meta Binja reads that repository's README through the GitHub API. Raw Markdown
URLs are also supported. Set `GITHUB_TOKEN` in your environment to raise
GitHub's unauthenticated API rate limit.

A simple JSON catalog is supported too:

```json
{
  "plugins": [
    {
      "name": "Example plugin",
      "repo_url": "https://github.com/example/binja-plugin",
      "description": "Example metadata"
    }
  ]
}
```

## Direct repository installs

Paste a repository URL into Meta Binja's search field:

```text
https://github.com/example/my-binja-plugin
```

If the URL matches a Binary Ninja-managed extension, Meta Binja keeps using the native lifecycle. Otherwise it opens a Git-backed management page.

## Security model

Installing a Binary Ninja plugin means trusting code that Binary Ninja may import and execute. Meta Binja does not automatically run arbitrary `setup.py`, shell scripts, or post-install hooks. Review third-party repositories before installing them.

## Current limitations

- Git-backed dependency installation is intentionally manual.
- Native/C++ build pipelines are not automated.
- Windows activation falls back to copying when symlink creation is unavailable.
- README retrieval covers GitHub, GitLab, Bitbucket, and Gitea/Forgejo hosts.

## Development

Run the tests from the repository root:

```sh
python3 -m unittest discover
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for implementation details.
