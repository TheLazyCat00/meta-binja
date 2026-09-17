# UI behavior

Meta Binja's manager runs in two places: the Binary Ninja sidebar, and a
standalone window opened from **Plugins → Meta Binja → Open Plugin Manager**.
Both host the same panel.

## Result table

Results are a sortable table with one row per plugin:

| Column | Contents |
| --- | --- |
| Name | The plugin's display name |
| Version | Installed version, or the catalog/extension version |
| Source | Native, Git, or Catalog — where the plugin comes from |
| Status | Available, Disabled, Enabled, or Update — its lifecycle state |

Source and status are separate columns because they are separate facts: a
native extension can be enabled, and a catalog entry can be installed from Git.
Both columns are color coded, and clicking a header sorts by that column.

A filter selector narrows the table to installed plugins, plugins with updates,
or plugins that are not installed. The count line beneath the table reports how
many rows are shown and how many plugins are installed overall.

## Detail page

Opening a row shows the plugin's README, rendered from Markdown, with relative
links and images resolved against the repository so screenshots load. Above it
sit the plugin's name, its source and status badges, and a fact line with the
author, version, star count, license, and last push date where the host
provides them. Actions are install/uninstall, update, and an enable toggle,
alongside buttons to open the repository in a browser or copy its URL.

When no README is available, the page falls back to the plugin's description
and a link to its repository.

## Search

The search field accepts normal text or a repository URL. Text search ranks
name matches above description matches; a URL resolves against known native and
catalog entries, and otherwise opens a Git-backed management page.

## Responsiveness

Git and network work runs on a background thread pool, so cloning, fetching,
and catalog downloads never block Binary Ninja's UI. Progress and errors appear
in the status line at the bottom of the panel rather than in modal dialogs;
uninstalling asks for confirmation first.

## Keyboard

- `Ctrl+F` focuses the search field.
- `Enter` in the search field opens the first result.
- `Esc` returns from a detail page to the table.
- `F5` refreshes catalogs and checks for updates.
