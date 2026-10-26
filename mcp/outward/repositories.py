"""Data access. Local and read-only: save files, the mod's JSON export, bundled wiki snapshots. No network."""
from __future__ import annotations

import gzip
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

CHAR_FILE = "Char0.defedc"
WORLD_FILE = "World.defedc"


# --- save format: gzip'd XML; items are XML-escaped XML documents inside <SyncData> ---

def tag(xml: str, name: str) -> str | None:
    match = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return match.group(1) if match else None


def sync_items(xml: str, section: str = "ItemList") -> list[str]:
    body = tag(xml, section) or ""
    return [html.unescape(s) for s in re.findall(r"<SyncData>(.*?)</SyncData>", body, re.S)]


def container_uid(item_xml: str) -> str | None:
    """Hierarchy '1<uid>;<slot>' means the item sits inside container <uid>."""
    hierarchy = (tag(item_xml, "Hierarchy") or "").split(";")[0]
    return hierarchy[1:] if hierarchy.startswith("1") else None


def xml_to_obj(el: ET.Element):
    """Generic XML -> dict / list / str. Nested <Item> XML inside SyncData is parsed too."""
    children = list(el)
    if not children:
        text = (el.text or "").strip()
        if el.tag == "SyncData" and text.startswith("<?xml"):
            return xml_to_obj(ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", text)))
        return text
    if len(children) > 1 and len({c.tag for c in children}) == 1:
        return [xml_to_obj(c) for c in children]
    return {c.tag: xml_to_obj(c) for c in children}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:  # also covers the mod's delete-then-rename window
        return default


class SaveRepository:
    """Save slots (one per character) and their timestamped snapshots, newest first.

    character: None = most recently played; int / digit string = index from list_characters;
               otherwise a name or slot UID (case-insensitive).
    snapshot:  0 = latest, 1 = the one before, ...; or a snapshot folder name like '20261025231406'.
    """

    def __init__(self, saves_dir: Path):
        self._saves_dir = saves_dir

    @staticmethod
    def _snaps(slot: Path) -> list[Path]:
        return sorted((p for p in slot.iterdir() if (p / CHAR_FILE).exists()), reverse=True)

    def slots(self) -> list[Path]:
        """Character slot folders, most recently saved first. Index = character number."""
        slots = [s for s in self._saves_dir.glob("*/Save_*") if self._snaps(s)]
        return sorted(slots, key=lambda s: self._snaps(s)[0].name, reverse=True)

    def slot(self, character: str | int | None = None) -> Path:
        slots = self.slots()
        if not slots:
            raise FileNotFoundError(f"no saves in {self._saves_dir}")
        if character is None or character == "":
            return slots[0]
        key = str(character).strip()
        if key.isdigit():
            if int(key) >= len(slots):
                raise IndexError(f"character {key} out of range (0..{len(slots) - 1})")
            return slots[int(key)]
        for s in slots:
            if key.lower() in (self.character_uid(s).lower(), self.character_name(self._snaps(s)[0]).lower()):
                return s
        raise FileNotFoundError(f"no character {character!r}")

    def snapshots(self, character: str | int | None = None) -> list[Path]:
        return self._snaps(self.slot(character))

    def snapshot(self, character: str | int | None = None, snapshot: str | int = 0) -> Path:
        snaps = self.snapshots(character)
        key = str(snapshot).strip()
        if len(key) <= 4 and key.isdigit():  # small number = age index; folder names are 14-digit timestamps
            if int(key) >= len(snaps):
                raise IndexError(f"snapshot {key} out of range (0..{len(snaps) - 1})")
            return snaps[int(key)]
        for s in snaps:
            if s.name == key:
                return s
        raise FileNotFoundError(f"no snapshot {snapshot!r}")

    def read(self, snap: Path, file: str) -> str:
        # Path(file).name: callers pass user input; never leave the snapshot folder.
        return gzip.decompress((snap / Path(file).name).read_bytes()).decode("utf-8-sig")

    def character_name(self, snap: Path) -> str:
        return tag(self.read(snap, CHAR_FILE), "Name") or "?"

    @staticmethod
    def character_uid(slot_or_snap: Path) -> str:
        slot = slot_or_snap if slot_or_snap.name.startswith("Save_") else slot_or_snap.parent
        return slot.name.removeprefix("Save_")

    @staticmethod
    def files(snap: Path) -> list[Path]:
        return sorted(snap.glob("*.de*"))

    @staticmethod
    def areas(snap: Path) -> list[Path]:
        return sorted(snap.glob("*.deenvc"))


class ItemRepository:
    """Item facts by object id. The game export (mod) overrides the bundled wiki snapshot field by field."""

    def __init__(self, game_export: Path, wiki_items: Path, wiki_names: Path):
        self._game_export = game_export
        self._wiki_items = wiki_items
        self._wiki_names = wiki_names
        self._db: dict[str, dict] = {}
        self._stamp: float | None = None

    def _load(self) -> dict[str, dict]:
        stamp = self._game_export.stat().st_mtime if self._game_export.exists() else None
        if self._db and stamp == self._stamp:
            return self._db
        db = {i: {"name": name} for i, name in _read_json(self._wiki_names, {}).items()}
        db.update({i: {**facts, "source": "wiki"} for i, facts in _read_json(self._wiki_items, {}).items()})
        for i, facts in _read_json(self._game_export, {}).items():
            db[i] = {**db.get(i, {}), **facts, "source": "game"}
        # by_name: a real item beats a skill of the same name
        self._names = {f["name"].lower(): f for f in sorted(db.values(), key=lambda f: not f.get("is_skill"))
                       if "name" in f}
        self._db, self._stamp = db, stamp
        return db

    def all(self) -> dict[str, dict]:
        return self._load()

    def by_name(self, name: str) -> dict:
        self._load()
        return self._names.get(name.lower(), {})

    def get(self, item_id: str) -> dict:
        return self._load().get(str(item_id)) or {"name": f"unknown:{item_id}"}

    def name(self, item_id: str) -> str:
        return self.get(item_id)["name"]

    def search(self, query: str, limit: int = 10) -> dict[str, dict]:
        db = self._load()
        query = query.strip()
        if query in db:
            return {query: db[query]}
        needle = query.lower()
        hits = [(i, facts) for i, facts in db.items() if needle in facts.get("name", "").lower()]
        hits.sort(key=lambda hit: (hit[1]["name"].lower() != needle, hit[1]["name"]))
        return dict(hits[:limit])

    def __len__(self) -> int:
        return len(self._load())


class RecipeRepository:
    """Recipes as {result, count, station, ingredients: [names]} plus groups ('Meat' accepts any meat).
    The mod's game export wins (exact ids, follows patches); the wiki snapshot is the fallback."""

    def __init__(self, wiki: Path, game_export: Path, items: ItemRepository):
        self._wiki, self._game, self._items = wiki, game_export, items
        self._data: dict | None = None
        self._stamp: float | None = None

    def _load(self) -> dict:
        stamp = self._game.stat().st_mtime if self._game.exists() else None
        if self._data is None or stamp != self._stamp:
            game = _read_json(self._game, None)
            self._data = self._from_game(game) if game else _read_json(self._wiki, {"recipes": [], "groups": {}})
            self._stamp = stamp
        return self._data

    def _from_game(self, raw: list[dict]) -> dict:
        by_tag: dict[str, set[str]] = {}
        for facts in self._items.all().values():
            if facts.get("is_skill"):
                continue
            for t in facts.get("tags") or ():
                by_tag.setdefault(t, set()).add(facts["name"])
        recipes, used = [], set()
        for r in raw:
            names = [self._items.name(i["id"]) if "id" in i else i.get("tag", "?") for i in r["ingredients"]]
            used.update(i["tag"] for i in r["ingredients"] if "tag" in i)
            first = r["results"][0]
            recipes.append({"result": self._items.name(first["id"]), "count": first["qty"],
                            "station": r["station"], "ingredients": names})
        return {"recipes": recipes, "groups": {t: sorted(by_tag.get(t, ())) for t in used}, "source": "game"}

    @property
    def recipes(self) -> list[dict]:
        return self._load()["recipes"]

    @property
    def groups(self) -> dict[str, list[str]]:
        return self._load()["groups"]

    @property
    def source(self) -> str:
        return self._load().get("source", "wiki")


class WikiRepository:
    """Wiki snapshots from data/fetch_wiki.py: places, quests, enemies, NPCs... (the game has no such data)."""
    KINDS = ("locations", "quests", "enemies", "npcs", "skills", "gathering", "effects", "buildings", "traps", "spells")

    def __init__(self, data_dir: Path):
        self._data = {k: _read_json(data_dir / f"wiki_{k}.json", {}) for k in self.KINDS}

    def data(self, kind: str) -> dict[str, dict]:
        return self._data[kind]

    def find(self, kind: str, query: str, limit: int = 10) -> dict[str, dict]:
        """Exact title (case-insensitive), else titles containing the query."""
        rows, needle = self._data[kind], query.strip().lower()
        exact = {t: r for t, r in rows.items() if t.lower() == needle}
        return exact or dict(sorted((t, r) for t, r in rows.items() if needle in t.lower())[:limit])


class LiveRepository:
    """Live state the mod rewrites every 15 s while the game runs."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict | None:
        try:
            age = time.time() - self.path.stat().st_mtime
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return {**data, "age_seconds": round(age)}
