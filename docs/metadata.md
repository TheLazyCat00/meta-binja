# Repository metadata

`meta_binja/metadata.py` fetches the content shown on a plugin's detail page.
It holds no Qt or Binary Ninja imports, so it is testable outside the
application, and every helper degrades to an empty result instead of raising.

## READMEs

For an installed Git plugin the README is read from the local checkout, so it
matches the revision on disk. Otherwise it is fetched from the host: GitHub
through its API, and GitLab, Bitbucket, and Gitea/Forgejo hosts through their
conventional raw paths.

Relative Markdown links and images are rewritten to absolute URLs before
rendering — images against the repository's raw-content URL, links against its
web view — because Qt renders the Markdown outside any page context.

## Repository facts

Star count, license, last push date, and archive state come from GitHub's
repository API. Other hosts contribute nothing, and the detail page simply
omits those facts.

## Caching

Catalogs, READMEs, and repository facts are cached as JSON beneath
`meta-binja/cache` in Binary Ninja's user directory, with a TTL per kind. The
Refresh button bypasses the cache. A corrupt or unwritable cache behaves like
an empty one.

Set `GITHUB_TOKEN` (or `GH_TOKEN`) in the environment to raise GitHub's
unauthenticated API rate limit.
