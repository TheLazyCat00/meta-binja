# Security

Meta Binja installs code that Binary Ninja may later import and execute. Only install repositories you trust.

The Git provider deliberately limits automation:

- It clones and updates repositories with Git.
- It does not execute `setup.py`, shell scripts, post-install hooks, or arbitrary build commands.
- Python `requirements.txt` dependencies are installed through Binary Ninja's configured dependency installer.
- Source-backed native/community entries use the same Git lifecycle as direct URLs; package-only entries fall back to Binary Ninja's Extension Manager.

Rendered READMEs come from remote repositories. Links open in the system browser only when clicked. Images are fetched over HTTPS only, from publicly routable hosts only — including across redirects — and bounded in count and size, so a README cannot aim the plugin at internal services. See [metadata.md](metadata.md) for the limits of that check.

Catalogs are discovery-only. Native/community catalog metadata may take presentation precedence for the same repository URL, but lifecycle ownership remains with the Git provider.
