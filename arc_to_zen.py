# /// script
# requires-python = ">=3.9"
# dependencies = ["lz4"]
# ///
"""Move your Arc sidebar into Zen Browser (macOS).

Arc favorites      -> Zen Essentials
Arc spaces         -> Zen workspaces (same names, Arc's colour)
Arc pinned folders -> Zen pinned folders (nested folders included)
Arc pinned tabs    -> Zen pinned tabs, with Arc's titles and cached icons

It reads Arc's own sidebar file (~/Library/Application Support/Arc/StorableSidebar.json)
and writes Zen's session file (zen-sessions.jsonlz4) in your Zen profile, after
saving a backup next to it. Zen must be fully quit (Cmd+Q) while it runs.

Usage:
  python3 arc_to_zen.py --dry-run     # show what would be imported, change nothing
  python3 arc_to_zen.py               # import
  python3 arc_to_zen.py --restore     # put back the Zen sidebar from before the import
"""
import argparse
import base64
import configparser
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

try:
    import lz4.block
except ImportError:
    sys.exit("Missing dependency: run  python3 -m pip install --user lz4  (or use  uv run arc_to_zen.py)")

HOME = os.path.expanduser("~")
ARC_DIR = os.path.join(HOME, "Library", "Application Support", "Arc")
ZEN_DIR = os.path.join(HOME, "Library", "Application Support", "zen")
SESSION = "zen-sessions.jsonlz4"
BACKUP = SESSION + ".before-arc-import"
MOZLZ4_MAGIC = b"mozLz40\0"
# Arc space icons are SF Symbol names or emoji; only the ones we can map are carried over.
ARC_SYMBOL_EMOJI = {"flash": "⚡"}


# ---------------------------------------------------------------- file helpers
def read_mozlz4(path):
    raw = open(path, "rb").read()
    if raw[:8] != MOZLZ4_MAGIC:
        sys.exit(f"{path} is not a mozLz4 file")
    return json.loads(lz4.block.decompress(raw[8:]))


def encode_mozlz4(data):
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    out = MOZLZ4_MAGIC + lz4.block.compress(blob)
    if lz4.block.decompress(out[8:]) != blob:
        sys.exit("internal error: compressed session does not decode back to the same data")
    return out


def zen_running():
    return subprocess.run(["pgrep", "-x", "zen"], capture_output=True).returncode == 0


def find_zen_profile():
    ini = os.path.join(ZEN_DIR, "profiles.ini")
    if not os.path.exists(ini):
        sys.exit("Zen profile not found. Install Zen, open it once, then quit it and run this again.")
    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read(ini)
    rel = None
    # The [Install...] section names the profile Zen actually opens.
    for sec in cp.sections():
        if sec.startswith("Install") and cp.has_option(sec, "Default"):
            rel = cp.get(sec, "Default")
            break
    if rel is None:
        for sec in cp.sections():
            if sec.startswith("Profile") and cp.get(sec, "Default", fallback="") == "1":
                rel = cp.get(sec, "Path")
    if rel is None:
        sys.exit("Could not tell which Zen profile is in use; pass it with --profile")
    return rel if os.path.isabs(rel) else os.path.join(ZEN_DIR, rel)


# ---------------------------------------------------------------- Arc reading
def pairs(flat):
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


class Arc:
    def __init__(self, arc_dir):
        path = os.path.join(arc_dir, "StorableSidebar.json")
        if not os.path.exists(path):
            sys.exit(f"Arc sidebar not found at {path}")
        c = json.load(open(path))["sidebar"]["containers"][1]
        self.items = dict(pairs(c["items"]))
        self.spaces = [s for _, s in pairs(c["spaces"])]
        self.top_apps = [cid for _, cid in pairs(c.get("topAppsContainerIDs", []))]
        self.fav_dir = os.path.join(arc_dir, "SidebarItemsFaviconCache")
        self.fav_files = set(os.listdir(self.fav_dir)) if os.path.isdir(self.fav_dir) else set()

    def icon(self, item_id):
        # Arc caches each sidebar item's favicon as a PNG named md5(item id).
        name = hashlib.md5(item_id.encode()).hexdigest()
        if name not in self.fav_files:
            return None
        data = open(os.path.join(self.fav_dir, name), "rb").read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode()

    def pinned_root(self, space):
        for key, cid in pairs(space.get("newContainerIDs") or space.get("containerIDs") or []):
            if isinstance(key, dict) and "pinned" in key:
                return self.items.get(cid)
        return None


def space_colour(space):
    try:
        bg = space["customInfo"]["windowTheme"]["background"]["single"]["_0"]["style"]["color"]["_0"]
        c = bg["blendedSingleColor"]["_0"]["color"]
        return [max(0, min(255, round(c[k] * 255))) for k in ("red", "green", "blue")]
    except (KeyError, TypeError):
        return None  # gradient or default theme: keep Zen's colour


def space_icon(space):
    icon = (space.get("customInfo") or {}).get("iconType") or {}
    for key, value in icon.items():
        if key == "icon" and value in ARC_SYMBOL_EMOJI:
            return ARC_SYMBOL_EMOJI[value]
        if isinstance(value, str) and value and not value.isascii() and len(value) <= 8:
            return value  # already an emoji
    return ""


# ---------------------------------------------------------------- Zen building
class Builder:
    def __init__(self, arc, zen):
        self.arc, self.zen = arc, zen
        self.now = int(time.time() * 1000)
        self.folder_seq = 0
        self.stats = {"essentials": 0, "folders": 0, "pinned": 0, "icons": 0, "skipped": {}}

    def sync_id(self):
        return f"{self.now}-{uuid.uuid4()}"

    def base_tab(self, url, title, img):
        entry = {"url": url, "title": title} if title else {"url": url}
        return {
            "entries": [entry],
            "lastAccessed": self.now,
            "pinned": True,
            "hidden": False,
            "zenWorkspace": None,
            "zenSyncId": self.sync_id(),
            "zenEssential": False,
            "zenDefaultUserContextId": None,
            "zenPinnedIcon": None,
            "zenIsEmpty": False,
            "zenHasStaticIcon": False,
            "zenGlanceId": None,
            "zenIsGlance": False,
            "_zenPinnedInitialState": {"entry": dict(entry), "image": img},
            "_zenIsActiveTab": True,
            "zenLiveFolderItemId": None,
            "searchMode": None,
            "userContextId": 0,
            "attributes": {},
            "index": 1,
            "image": img or "",
        }

    def tab(self, item_id, workspace=None, group_id=None, essential=False):
        it = self.arc.items[item_id]
        t = it["data"]["tab"]
        url = t.get("savedURL")
        if not url:
            return None
        page_title = t.get("savedTitle") or url
        img = self.arc.icon(item_id)
        self.stats["icons"] += bool(img)
        out = self.base_tab(url, page_title, img)
        if essential:
            out["zenEssential"] = True
            out["zenDefaultUserContextId"] = "true"
        else:
            out["zenWorkspace"] = workspace
        if group_id:
            out["groupId"] = group_id
        # Arc shows its own (renamed or tidied) title when the item has one.
        if it.get("title") and it["title"] != page_title:
            out["zenStaticLabel"] = it["title"]
        return out

    def skip(self, it):
        kind = ",".join(sorted((it.get("data") or {}).keys())) or "unknown"
        self.stats["skipped"][kind] = self.stats["skipped"].get(kind, 0) + 1

    def child_tabs(self, item_id):
        """Tabs directly under an item; split views are flattened into their tabs."""
        it = self.arc.items[item_id]
        data = it.get("data") or {}
        if "tab" in data:
            return [item_id]
        if "splitView" in data:
            return [c for c in it.get("childrenIds", []) if "tab" in (self.arc.items.get(c, {}).get("data") or {})]
        return []

    def folder(self, item_id, workspace, parent_id, prev_info, tabs_out, folders_out, groups_out):
        it = self.arc.items[item_id]
        self.folder_seq += 1
        fid = f"{self.now}-{self.folder_seq}"
        empty = self.base_tab("about:blank", None, None)
        empty.update(zenWorkspace=workspace, groupId=fid, zenIsEmpty=True, image="")
        empty["_zenPinnedInitialState"] = {"entry": {"url": "about:blank"}, "image": None}
        own_tabs = [empty]
        subfolders = []
        prev = {"type": "start", "id": None}
        for cid in it.get("childrenIds", []):
            child = self.arc.items.get(cid)
            if child is None:
                continue
            if "list" in (child.get("data") or {}):
                subfolders.append((cid, prev))
                prev = {"type": "group", "id": None, "_arc": cid}  # id filled in once the subfolder exists
                continue
            tabs = [self.tab(t, workspace, fid) for t in self.child_tabs(cid)]
            tabs = [t for t in tabs if t]
            if not tabs:
                self.skip(child)
                continue
            own_tabs += tabs
            prev = {"type": "tab", "id": tabs[-1]["zenSyncId"]}
        # A folder's own tabs must be contiguous; subfolders follow and Zen nests them via parentId.
        tabs_out += own_tabs
        self.stats["pinned"] += len(own_tabs) - 1
        self.stats["folders"] += 1
        folders_out.append(
            {
                "pinned": True,
                "splitViewGroup": False,
                "id": fid,
                "name": it.get("title") or "Folder",
                "collapsed": True,
                "saveOnWindowClose": True,
                "parentId": parent_id,
                "prevSiblingInfo": prev_info,
                "emptyTabIds": [empty["zenSyncId"]],
                "userIcon": "",
                "workspaceId": workspace,
            }
        )
        groups_out.append(
            {
                "pinned": True,
                "splitView": False,
                "id": fid,
                "name": it.get("title") or "Folder",
                "color": "zen-workspace-color",
                "collapsed": True,
                "saveOnWindowClose": True,
            }
        )
        arc_to_fid = {}
        for cid, sub_prev in subfolders:
            if sub_prev.get("_arc"):
                sub_prev = {"type": "group", "id": arc_to_fid[sub_prev["_arc"]]}
            arc_to_fid[cid] = self.folder(cid, workspace, fid, sub_prev, tabs_out, folders_out, groups_out)
        return fid

    def build(self):
        arc, zen = self.arc, self.zen
        spaces = zen.get("spaces") or []
        if not spaces:
            sys.exit("Zen's session has no workspaces yet. Open Zen once, quit it, then run this again.")
        theme_template = spaces[0].get("theme")

        # Workspaces: an Arc space reuses a Zen workspace with the same name; the first Arc
        # space takes over Zen's first workspace; any other Arc space becomes a new workspace.
        by_name = {s["name"]: s for s in spaces}
        claimed = set()
        mapping = []
        for n, sp in enumerate(arc.spaces):
            title = sp.get("title") or f"Space {n + 1}"
            ws = by_name.get(title)
            if ws is None and n == 0 and spaces[0]["uuid"] not in claimed:
                ws = spaces[0]
            if ws is None:
                ws = {"uuid": "{" + str(uuid.uuid4()) + "}", "name": title,
                      "theme": copy.deepcopy(theme_template), "containerTabId": 0, "hasCollapsedPinnedTabs": False}
                spaces.append(ws)
            claimed.add(ws["uuid"])
            ws["name"] = title
            icon = space_icon(sp)
            if icon:
                ws["icon"] = icon
            rgb = space_colour(sp)
            if rgb and ws.get("theme", {}).get("gradientColors"):
                ws["theme"] = copy.deepcopy(ws["theme"])
                ws["theme"]["gradientColors"] = [dict(ws["theme"]["gradientColors"][0], c=rgb, isCustom=False)]
            mapping.append((sp, ws["uuid"]))

        # Essentials: Arc favorites of every Arc profile, skipping URLs already in Zen's Essentials.
        old = zen.get("tabs") or []
        have = {t["entries"][-1]["url"] for t in old if t.get("zenEssential") and t.get("entries")}
        essentials = []
        for cid in arc.top_apps:
            for tid in arc.items.get(cid, {}).get("childrenIds", []):
                for t in self.child_tabs(tid):
                    tab = self.tab(t, essential=True)
                    if tab and tab["entries"][0]["url"] not in have:
                        have.add(tab["entries"][0]["url"])
                        essentials.append(tab)
        self.stats["essentials"] = len(essentials)

        # Pinned tabs and folders, space by space, in Arc's order.
        pinned_tabs, folders, groups = [], [], []
        for sp, ws in mapping:
            root = arc.pinned_root(sp)
            if not root:
                continue
            prev = {"type": "start", "id": None}
            for cid in root.get("childrenIds", []):
                child = arc.items.get(cid)
                if child is None:
                    continue
                if "list" in (child.get("data") or {}):
                    fid = self.folder(cid, ws, None, prev, pinned_tabs, folders, groups)
                    prev = {"type": "group", "id": fid}
                    continue
                tabs = [self.tab(t, ws) for t in self.child_tabs(cid)]
                tabs = [t for t in tabs if t]
                if not tabs:
                    self.skip(child)
                    continue
                pinned_tabs += tabs
                self.stats["pinned"] += len(tabs)
                prev = {"type": "tab", "id": tabs[-1]["zenSyncId"]}

        old_essentials = [t for t in old if t.get("zenEssential")]
        old_pinned = [t for t in old if t.get("pinned") and not t.get("zenEssential")]
        old_unpinned = [t for t in old if not t.get("pinned")]
        zen["tabs"] = essentials + old_essentials + pinned_tabs + old_pinned + old_unpinned
        zen["folders"] = folders + (zen.get("folders") or [])
        zen["groups"] = groups + (zen.get("groups") or [])
        zen["spaces"] = spaces
        self.check()
        return zen

    def check(self):
        zen = self.zen
        tabs = zen["tabs"]
        first_unpinned = next((i for i, t in enumerate(tabs) if not t.get("pinned")), len(tabs))
        assert all(not t.get("pinned") for t in tabs[first_unpinned:]), "pinned tab after an unpinned one"
        group_ids = [g["id"] for g in zen["groups"]]
        assert sorted(group_ids) == sorted(f["id"] for f in zen["folders"]), "folders and groups differ"
        assert all(t["groupId"] in group_ids for t in tabs if t.get("groupId")), "tab in a missing folder"
        ids = [t.get("zenSyncId") for t in tabs if t.get("zenSyncId")]
        assert len(ids) == len(set(ids)), "duplicate tab id"
        for f in zen["folders"]:
            assert all(e in ids for e in f.get("emptyTabIds", [])), f"folder {f['name']} lost its placeholder tab"
            if f.get("parentId"):
                assert f["parentId"] in group_ids, f"folder {f['name']} has a missing parent"
        # each folder's tabs must sit next to each other, or Firefox splits the group
        seen, last = set(), None
        for t in tabs:
            g = t.get("groupId")
            if g != last and g in seen:
                raise AssertionError("a folder's tabs are not contiguous")
            if g:
                seen.add(g)
            last = g
        spaces = {s["uuid"] for s in zen["spaces"]}
        assert all(t.get("zenWorkspace") in spaces for t in tabs if t.get("pinned") and not t.get("zenEssential")), \
            "pinned tab in a missing workspace"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Move your Arc sidebar into Zen Browser (macOS).")
    ap.add_argument("--dry-run", action="store_true", help="show what would be imported, change nothing")
    ap.add_argument("--restore", action="store_true", help="undo: put back Zen's sidebar from before the import")
    ap.add_argument("--force", action="store_true", help="import again even though a previous import exists")
    ap.add_argument("--profile", help="path to the Zen profile folder (default: the one Zen opens)")
    ap.add_argument("--arc-dir", default=ARC_DIR, help=argparse.SUPPRESS)
    args = ap.parse_args()

    profile = args.profile or find_zen_profile()
    session = os.path.join(profile, SESSION)
    backup = os.path.join(profile, BACKUP)
    print(f"Zen profile: {profile}")

    if not args.dry_run and zen_running():
        sys.exit("Zen is running. Quit it with Cmd+Q (not just closing the window), then run this again.")

    if args.restore:
        if not os.path.exists(backup):
            sys.exit("No backup found, so there is nothing to restore.")
        shutil.copy2(backup, session)
        os.remove(backup)
        print("Restored Zen's sidebar from before the import. Open Zen to see it.")
        return

    if not os.path.exists(session):
        sys.exit(f"{SESSION} not found. Open Zen once and quit it; if it is still missing, update Zen "
                 "(older versions keep the sidebar in a different format).")
    if os.path.exists(backup) and not args.force and not args.dry_run:
        sys.exit("Arc was already imported into this profile. Run with --restore to undo it first, "
                 "or --force to import again on top (you would get duplicates).")

    arc = Arc(args.arc_dir)
    builder = Builder(arc, read_mozlz4(session))
    zen = builder.build()
    st = builder.stats

    print(f"Workspaces:  {', '.join((s.get('icon', '') + ' ' + s['name']).strip() for s in zen['spaces'])}")
    print(f"Essentials:  {st['essentials']} added")
    print(f"Folders:     {st['folders']}")
    print(f"Pinned tabs: {st['pinned']}  ({st['icons']} with Arc's icon; the rest get theirs when first opened)")
    if st["skipped"]:
        print(f"Skipped (no Zen equivalent): {st['skipped']}")

    if args.dry_run:
        print("\nDry run: nothing was changed.")
        return

    blob = encode_mozlz4(zen)
    if not os.path.exists(backup):
        shutil.copy2(session, backup)
    tmp = session + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, session)
    print(f"\nDone. Backup of your previous Zen sidebar: {backup}")
    print("Open Zen to see your Arc sidebar. To undo: python3 arc_to_zen.py --restore")


if __name__ == "__main__":
    main()
