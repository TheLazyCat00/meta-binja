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
web view — because Qt renders the Markdown outside any page context. A leading
slash resolves from the repository root, as it does on GitHub.

Images in a README are addresses chosen by its author, so they are fetched only
over HTTPS, only from hosts that resolve to publicly routable addresses, and
only up to a bounded count and size. Redirects are re-checked at each hop. The
address is resolved once for the check and again by the connection, so this
does not defeat a DNS-rebinding attacker; it does keep a README from pointing
the plugin at the loopback or private services on the user's network.

## Repository facts

Star count, license, last push date, and archive state come from GitHub's
repository API. Other hosts contribute nothing, and the detail page simply
omits those facts.

## Caching

Catalogs, READMEs, and repository facts are cached as JSON beneath
`meta-binja/cache` in Binary Ninja's user directory, with a TTL per kind. The
Refresh button bypasses the cache, including for the plugin page that is open
at the time. A corrupt or unwritable cache behaves like an empty one.

Each update reads, modifies, and rewrites its cache file, so writes to one path
are serialized across background tasks and each lands through its own temporary
file.

Set `GITHUB_TOKEN` (or `GH_TOKEN`) in the environment to raise GitHub's
unauthenticated API rate limit.
