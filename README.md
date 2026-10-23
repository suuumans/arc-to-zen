# Arc to Zen

Move your Arc sidebar into [Zen Browser](https://zen-browser.app) on macOS, so Zen
looks the way Arc did: favorites at the top, your spaces, and your pinned folders and
tabs in the same order.

Zen's built-in importer doesn't list Arc. Arc's own **Export** gives you a Markdown file
that Zen can't read, and a bookmarks import only fills the bookmarks menu, not the sidebar.
This script reads Arc's sidebar file directly and writes it into Zen's sidebar.

## What moves

| In Arc | In Zen |
|---|---|
| Favorites (the icon grid at the top) | **Essentials** |
| Spaces | **Workspaces**, with the same names, Arc's colour and its ⚡ / emoji icon where possible |
| Pinned folders, including folders inside folders | **Pinned folders**, nested the same way, starting closed |
| Pinned tabs | **Pinned tabs**, in Arc's order, with Arc's tab names and Arc's cached icons |
| Split views | The tabs in them, as normal pinned tabs |

Nothing already in Zen is removed. Your existing Essentials, folders and tabs stay, after
the imported ones.

## What does not move

- **Passwords.** Move them yourself with a CSV file; see [Passwords](#passwords) below. The script never touches them.
- **History, cookies and open logins.** You'll need to sign in to sites again.
- **Today (unpinned) tabs, Arc notes, easels and Boosts.** Zen has no equivalent.
- **Extensions.** Arc uses Chrome extensions and Zen uses Firefox add-ons. Install the Firefox versions from [addons.mozilla.org](https://addons.mozilla.org).
- **Passkeys.** They can't be exported. Set them up again in Zen.

## Requirements

- A Mac with **Arc** still installed and your data in it. The script reads Arc's files on this Mac, not your Arc account online.
- **A recent Zen**, opened at least once. It was tested on Zen 1.23.4b; older versions that don't have a `zen-sessions.jsonlz4` file aren't supported.
- **Python 3.** Type `python3 --version` in Terminal. If macOS offers to install developer tools, accept, then try again.
- The **`lz4`** Python package. Step 2 installs it.

## Step by step

**1. Download this project.** Click the green **Code** button → **Download ZIP**, and unzip it. Or use git:

```bash
git clone https://github.com/suuumans/arc-to-zen.git
```

**2. Open Terminal in the project folder and install `lz4`:**

```bash
cd ~/Downloads/arc-to-zen-main      # or wherever the folder is
python3 -m pip install --user lz4
```

If you use [uv](https://docs.astral.sh/uv/), skip the install and replace `python3` with
`uv run` in the commands below. uv installs `lz4` by itself.

**3. Open Zen once, then quit it with ⌘Q.** Closing the window isn't enough: Zen saves its
sidebar when it quits and would overwrite the import.

**4. Preview what will be imported.** This changes nothing:

```bash
python3 arc_to_zen.py --dry-run
```

You should see something like:

```
Zen profile: /Users/you/Library/Application Support/zen/Profiles/xxxx.Default (release)
Workspaces:  ⚡ Personal, Work
Essentials:  12 added
Folders:     8
Pinned tabs: 61  (49 with Arc's icon; the rest get theirs when first opened)
Dry run: nothing was changed.
```

**5. Import:**

```bash
python3 arc_to_zen.py
```

**6. Open Zen.** Your Arc sidebar is there. Switch workspaces from the icons at the bottom
of the sidebar.

## Undo

Quit Zen (⌘Q) and run:

```bash
python3 arc_to_zen.py --restore
```

This puts back Zen's sidebar exactly as it was before the import. The script made a backup
for this before changing anything. It's called `zen-sessions.jsonlz4.before-arc-import`
and sits in your Zen profile folder.

## Passwords

Your passwords never go through this script. You move them with Arc's and Zen's own buttons:

1. In Arc, type `arc://password-manager/settings` in the address bar. If that doesn't open,
   try `chrome://password-manager/settings`.
2. Click **Export passwords** → **Download file**. Arc may ask for your Mac password.
3. In Zen, open **Settings** → **Import data**, choose **Passwords from CSV file**, and pick
   the downloaded file.
4. **Delete the CSV file and empty the Bin.** It holds every password in plain text.

## Troubleshooting

| Message or problem | What to do |
|---|---|
| `Zen is running` | Quit Zen with ⌘Q, not just the window, and run again. |
| `Arc was already imported into this profile` | You've run it before. Use `--restore` first, then import again. `--force` imports on top, which gives you duplicates. |
| `Zen profile not found` / `zen-sessions.jsonlz4 not found` | Open Zen once, quit it, and run again. If it still fails, update Zen. |
| You have more than one Zen profile | Pass the right one: `python3 arc_to_zen.py --profile "/Users/you/Library/Application Support/zen/Profiles/xxxx.name"` |
| Some tabs have no icon | Arc only caches icons for some tabs. The rest appear the first time you open each tab. |
| The colour looks off | Change it in Zen's theme settings for that workspace. |
| `Missing dependency` | Run `python3 -m pip install --user lz4`, or use `uv run`. |

## How it works

- Arc keeps its sidebar in `~/Library/Application Support/Arc/StorableSidebar.json`.
  Spaces, favorites, folders and tabs are items in that file, linked by IDs.
- Arc caches sidebar icons in `SidebarItemsFaviconCache/`. Each one is a PNG named after the MD5 of the item's ID.
- Recent versions of Zen keep the sidebar in `zen-sessions.jsonlz4` in your profile.
  That file is JSON compressed with Mozilla's `mozLz4` format.
  - Essentials are pinned tabs marked `zenEssential`.
  - Folders are tab groups, plus a `folders` entry with a parent ID for nesting.
  - Workspaces are listed under `spaces`.
- The script builds those entries from Arc's data and checks the result before writing it:
  - Pinned tabs come before unpinned ones.
  - Every folder has its tabs next to each other, its placeholder tab, and a parent that exists.
  - No tab ID appears twice.
- It then writes the file in one step, after saving a backup.

Tested on macOS 26.6 with Arc 1.169 and Zen 1.23.4b. Zen's file format may change in future
versions; if an import looks wrong, use `--restore` and open an issue.

## Disclaimer

This is an unofficial tool. It isn't affiliated with The Browser Company (Arc) or the Zen
Browser team. It changes Zen's sidebar file in your profile. A backup is always kept, but
use it at your own risk.

## License

[MIT](LICENSE)
