# Git-backed plugins

Meta Binja uses one Git lifecycle for direct repository installs and source-backed plugins discovered through Binary Ninja's native/community catalogs.

Direct installs accept HTTPS and SSH repository URLs. Cleartext HTTP URLs and URLs containing embedded web credentials are rejected before cloning or persistence; private repositories should use Git's external credential or SSH configuration instead.

Checkouts live in Meta Binja's private repository store. Enabled plugins are exposed from Binary Ninja's normal user plugin directory using the repository's case-preserving basename.

If Binary Ninja's catalog provides a plugin `subdir`, Meta Binja activates that directory instead of the repository root. This supports monorepos such as entries whose Binary Ninja integration lives under `binjastub` or another nested path. The subdirectory is validated as repository-relative and persisted in `managed.json`.

The provider prefers directory symlinks and falls back to a transactionally replaced copy where symlink creation is unavailable. Updates use `git pull --ff-only --recurse-submodules`; local divergence is never overwritten automatically.

Before activation and after updates, Meta Binja installs `requirements.txt` through Binary Ninja's configured Python dependency installer. Both the repository root and active plugin subdirectory are checked, so shared and integration-specific dependencies are supported.
