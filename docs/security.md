# Security

Meta Binja installs code that Binary Ninja may later import and execute. Only install repositories you trust.

The Git provider deliberately limits automation:

- It clones and updates repositories with Git.
- It does not execute `setup.py`, shell scripts, post-install hooks, or arbitrary build commands.
- Python dependency installation for Git-backed plugins is manual in the MVP.
- Native Binary Ninja extensions keep using Binary Ninja's own Extension Manager lifecycle and dependency handling.

Rendered READMEs come from remote repositories. Links open in the system browser only when clicked. Images are fetched over HTTPS only, from publicly routable hosts only — including across redirects — and bounded in count and size, so a README cannot aim the plugin at internal services. See [metadata.md](metadata.md) for the limits of that check.

Catalogs are discovery-only and cannot override a native extension that resolves to the same canonical repository URL.
