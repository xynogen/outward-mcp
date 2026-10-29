"""Use cases behind the MCP tools. Logic only; all data comes through the repositories."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .repositories import (CHAR_FILE, WORLD_FILE, ItemRepository, LiveRepository, RecipeRepository,
                           SaveRepository, WikiRepository, container_uid, sync_items, tag, xml_to_obj)
from .search import SearchIndex, tokens, without_ids, words

VITALS = ("Health", "BurntHealth", "Stamina", "BurntStamina", "Mana", "BurntMana",
          "Food", "Drink", "Sleep", "Corruption")
BUILDING_RESOURCES = ("Funds", "Food", "Timber", "Stone")
NO_STATION = ("None", "Survival")
STATION_ITEM = {"Cooking Pot": "Cooking Pot", "Cooking": "Cooking Pot",
                "Alchemy Kit": "Alchemy Kit", "Alchemy": "Alchemy Kit"}
MAX_CRAFTS = 99
WATER_CONTAINER = "Waterskin"  # its save Quantity is sips of water
LIVE_FRESH_S = 120  # older live.json = game not running
LOW = 0.25  # warn below this share of max
NEED_MAX = 1000  # save scale of Food / Drink / Sleep


def item_location(hierarchy: str | None, char_uid: str) -> str:
    h = re.sub(r";\d+$", "", hierarchy or "")
    if h.startswith("1Stash_"):
        return "stash"
    if h.startswith("1Pouch_"):
        return "pouch"
    if h.endswith("_Content"):
        return "backpack"
    if h == f"2{char_uid}":
        return "equipped"
    if h == f"3{char_uid}":
        return "skill"
    return "other:" + h


def _number(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class SaveRef:
    """Which save to read. See SaveRepository for the accepted values."""
    character: str | int | None = None
    snapshot: str | int = 0


LOCATIONS = ("equipped", "pouch", "backpack", "stash", "skill")


class OutwardService:
    def __init__(self, saves: SaveRepository, items: ItemRepository,
                 recipes: RecipeRepository, live: LiveRepository, wiki: WikiRepository):
        self.saves = saves
        self.items = items
        self.recipes = recipes
        self.live = live
        self.wiki = wiki
        self._search_index = None
        self._search_sources = None

    def _snap(self, ref: SaveRef) -> Path:
        return self.saves.snapshot(ref.character, ref.snapshot)

    # --- character ---

    def characters(self) -> list[dict]:
        result = []
        for index, slot in enumerate(self.saves.slots()):
            snaps = self.saves.snapshots(index)
            result.append({"character": index, "name": self.saves.character_name(snaps[0]),
                           "uid": self.saves.character_uid(slot), "snapshots": len(snaps),
                           "latest": snaps[0].name})
        return result

    def snapshot_list(self, ref: SaveRef, limit: int = 20) -> dict:
        snaps = self.saves.snapshots(ref.character)[:limit]
        rows = []
        for index, snap in enumerate(snaps):
            xml = self.saves.read(snap, CHAR_FILE)
            hours = float(tag(self.saves.read(snap, WORLD_FILE), "GameTime") or 0)
            rows.append({"snapshot": index, "id": snap.name, "day": int(hours // 24) + 1,
                         "area": tag(xml, "AreaName"), "health": round(float(tag(xml, "Health") or 0), 1),
                         "silver": int(tag(xml, "Money") or 0)})
        return {"character": self.saves.character_name(snaps[0]), "snapshots": rows}

    def inventory(self, ref: SaveRef = SaveRef(), include_skills: bool = False,
                  locations: list[str] | None = None) -> dict:
        snap = self._snap(ref)
        xml = self.saves.read(snap, CHAR_FILE)
        uid = self.saves.character_uid(snap)
        wanted = set(locations or LOCATIONS) - (set() if include_skills or locations else {"skill"})
        rows = [self._item_row(raw, uid) for raw in sync_items(xml)]
        items = [r for r in rows if r and (r["location"] in wanted or r["location"].startswith("other:"))]
        return {
            "character": tag(xml, "Name"),
            "save": str(snap),
            "area": tag(xml, "AreaName"),
            "vitals": {v: round(float(tag(xml, v) or 0), 1) for v in VITALS},
            "status": re.findall(r"<Identifier[^>]*>([^<]+)", tag(xml, "StatusList") or ""),
            "silver": int(tag(xml, "Money") or 0),
            "stashed_silver": int(tag(xml, "StashedMoney") or 0),
            "items": items,
        }

    def _item_row(self, raw: str, char_uid: str) -> dict | None:
        item_id = tag(raw, "ID")
        if not item_id:
            return None
        row = {"id": item_id, "location": item_location(tag(raw, "Hierarchy"), char_uid),
               "qty": int(tag(raw, "Quantity") or 1)}
        if durability := tag(raw, "Durability"):
            row["durability"] = float(durability)
        facts = {k: v for k, v in self.items.get(item_id).items() if k != "description"}
        return {**facts, **row}  # save values (qty, durability) win over item facts

    def state(self, ref: SaveRef = SaveRef(), sections: list[str] | None = None, source: str = "auto",
              include_stash: bool = False, offset: int = 0, limit: int = 20, details: bool = False) -> dict:
        sections = ["vitals"] if sections is None else sections
        inv = self.inventory(ref, include_skills="skills" in sections)
        live = self._live() if source != "save" and str(ref.snapshot) == "0" else None
        if live and live.get("character", "").casefold() != inv["character"].casefold():
            live = None
        if source == "live" and not live:
            raise ValueError("No fresh live data matching this character/latest snapshot; use source=save or auto")
        current = live or inv
        result = {"character": inv["character"], "source": "live" if live else "save",
                  "snapshot": Path(inv["save"]).name, "area": self._place(current["area"])[0],
                  "silver": current["silver"]}
        result["stashed_silver"] = {"source": "save", "amount": inv["stashed_silver"]}
        if live:
            result["age_seconds"] = live["age_seconds"]
        if "vitals" in sections:
            result["vitals"] = current["vitals"] if live else {
                re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower(): v for k, v in inv["vitals"].items()}
            result["status"] = {"source": "save", "effects": inv["status"]}
            if live:
                result["needs"] = {"source": "save", **{k: v for k, v in inv["vitals"].items()
                                                          if k in ("Food", "Drink", "Sleep", "Corruption")}}
            result["needs_scale"] = NEED_MAX
        if "skills" in sections:
            result["skills"] = {"source": "save", "items": [
                without_ids(r) if details else {"id": str(r["id"]), "name": r["name"]}
                for r in inv["items"] if r["location"] == "skill"]}
        if "inventory" in sections:
            rows, equipped = [], set()
            for row in current["items"]:
                if row["location"] == "skill":
                    continue
                if row["location"] == "stash" and not include_stash:
                    continue
                if row["location"] == "equipped":
                    identity = (row.get("id"), row["name"])
                    if identity in equipped:
                        continue
                    equipped.add(identity)
                facts = self.items.by_name(row["name"])
                item = {k: v for k, v in row.items() if k in {
                    "name", "location", "qty", "durability", "max_durability", "effects", "type"}}
                item["id"] = str(row["id"])
                if "max_durability" not in item and facts.get("max_durability", 0) > 0:
                    item["max_durability"] = facts["max_durability"]
                item.update(_freshness(row, facts))
                if row["name"] == WATER_CONTAINER and not live:
                    item.update(qty=1, water_sips=row["qty"])
                rows.append(item)
            rows.sort(key=lambda r: (r.get("estimated_days_left", float("inf")), r["name"]))
            result["inventory"] = {"total": len(rows), "offset": offset,
                "next_offset": offset + limit if offset + limit < len(rows) else None,
                "expiry_basis": "estimated in-game days at base decay; preservation modifiers not included",
                "items": [r if details else {"id": r["id"], "name": r["name"]}
                          for r in rows[offset:offset + limit]]}
        if "world" in sections:
            world = self.world(ref)
            world["visited_areas"] = [self._place(scene)[0] for scene in world["visited_areas"]]
            result["world"] = {**world, "source": "save", "quest_deadlines": "not derived from save events"}
        if "nearby" in sections:
            result["nearby"] = self.nearby_enemies() if live else {"available": False,
                "reason": "Requires fresh live data for the selected character/latest snapshot"}
        return result

    def live_state(self, sections: list[str] | None = None) -> dict:
        live = self.live.read()
        if not live:
            return {"error": "No live data. Start the game with the OutwardDataExport mod."}
        if sections is None:
            return live
        result = {k: v for k, v in live.items() if k not in ("items", "nearby")}
        result.update(source="live", stale=live.get("age_seconds", 1e9) > LIVE_FRESH_S,
                      area=self._place(live.get("area", ""))[0], item_entries=len(live.get("items", [])))
        if "items" in (sections or []):
            result["items"] = without_ids(live.get("items", []))
        if "nearby" in (sections or []):
            result["nearby"] = self.nearby_enemies().get("enemies", [])
        return result

    # --- raw save access ---

    def save_files(self, ref: SaveRef = SaveRef()) -> dict:
        snap = self._snap(ref)
        files = []
        for f in self.saves.files(snap):
            root = ET.fromstring(self.saves.read(snap, f.name).encode())
            sections = {c.tag: len(c) or (c.text or "").strip()[:40] for c in root}
            files.append({"file": f.name, "root": root.tag, "sections": sections})
        return {"save": str(snap), "files": files}

    def read_save_file(self, file: str, section: str | None = None, ref: SaveRef = SaveRef(),
                       offset: int = 0, limit: int = 100):
        if section and not re.fullmatch(r"\w+", section):  # section goes into an XPath
            return {"error": f"invalid section name {section!r}"}
        root = ET.fromstring(self.saves.read(self._snap(ref), file).encode())
        if not section:
            return {c.tag: f"<{len(c)} entries>" if len(c) > 3 else xml_to_obj(c) for c in root}
        el = root.find(f".//{section}")
        if el is None:
            return {"error": f"no section {section!r}", "sections": [c.tag for c in root]}
        data = xml_to_obj(el)
        if isinstance(data, list):
            return {"total": len(data), "offset": offset, "items": data[offset:offset + limit]}
        return data

    # --- world ---

    def world(self, ref: SaveRef = SaveRef()) -> dict:
        snap = self._snap(ref)
        xml = self.saves.read(snap, WORLD_FILE)
        quests = [{"id": tag(q, "ID"), "name": self.items.name(tag(q, "ID")),
                   "status": "done" if "QuestCompletionTime" in q else "active"}
                  for q in sync_items(xml, "QuestList")]
        hours = float(tag(xml, "GameTime") or 0)
        return {
            "day": int(hours // 24) + 1,
            "hour": round(float(tag(xml, "TOD") or 0), 2),
            "game_time_h": round(hours, 1),
            "season_id": tag(xml, "SeasonID"),
            "quests": quests,
            "quest_events_set": len(re.findall(r"<Identifier", tag(xml, "QuestEventList") or "")),
            "defeats": tag(xml, "DefeatCountSaveData"),
            "building": {k: tag(xml, "Building" + k) for k in BUILDING_RESOURCES},
            "visited_areas": [f.stem for f in self.saves.areas(snap)],
        }

    def merchants(self, area: str | None = None, ref: SaveRef = SaveRef()) -> list[dict]:
        snap = self._snap(ref)
        scene = self._resolve_area(area) if area else None
        found = []
        for area_file in self.saves.areas(snap):
            if scene and scene.casefold() != area_file.stem.casefold():
                continue
            found += self._area_merchants(area_file.stem, sync_items(self.saves.read(snap, area_file.name)))
        return found

    def _area_merchants(self, area: str, items: list[str]) -> list[dict]:
        pouches = {}
        for raw in items:
            uid = tag(raw, "UID") or ""
            if not uid.startswith("MerchantPouch_"):
                continue
            path = tag(raw, "Hierarchy") or ""
            who = re.search(r"_Merchants/([^/]+)", path) or re.search(r"/([^/]+)/MerchantTemplate", path)
            merchant = who.group(1) if who else path
            role = merchant.removeprefix("HumanSNPC_")
            if role != merchant:
                merchant = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", role)
            elif "MerchantSlaveVendavel" in merchant:
                merchant = "Vendavel prison merchant"
            elif merchant == "UNPC_WatcherFourthA":
                merchant = "Fourth Watcher"
            else:
                merchant = re.sub(r"^(?:UNPC|HumanSNPC)_", "", merchant).replace("_", " ")
            pouches[uid] = {"area": area, "merchant": merchant,
                            "silver": _number(r"MerchantPouchContainedSilver/(\d+)", raw), "stock": Counter()}
        for raw in items:
            if (parent := container_uid(raw)) in pouches:
                pouches[parent]["stock"][tag(raw, "ID")] += 1
        result = []
        for pouch in pouches.values():
            if not (pouch["stock"] or pouch["silver"]):
                continue
            stock = [{"name": self.items.name(i), "count": n, "buy": self.items.get(i).get("buy")}
                     for i, n in pouch["stock"].items()]
            result.append({**pouch, "stock": sorted(stock, key=lambda s: s["name"])})
        return result

    def area_loot(self, area: str, ref: SaveRef = SaveRef()) -> dict:
        snap = self._snap(ref)
        scene = self._resolve_area(area)
        area_file = next((f for f in self.saves.areas(snap) if scene.casefold() == f.stem.casefold()), None)
        if not area_file:
            return {"error": f"area {area!r} not visited"}
        items = sync_items(self.saves.read(snap, area_file.name))
        chests = {tag(raw, "UID"): {"chest_id": tag(raw, "ID"),
                                    "looted_by_you": "TreasureChestReceivedIDs" in raw,
                                    "silver": _number(r"ContainedSilver/(\d+)", raw) or 0, "items": []}
                  for raw in items if "TreasureChest" in raw}
        ground = []
        for raw in items:
            if (parent := container_uid(raw)) in chests:
                chests[parent]["items"].append(self.items.name(tag(raw, "ID")))
            elif not tag(raw, "Hierarchy") and tag(raw, "Position") and "TreasureChest" not in raw:
                ground.append(self.items.name(tag(raw, "ID")))
        return {"area": area_file.stem, "chests_total": len(chests),
                "chests_with_loot": [c for c in chests.values() if c["items"] or c["silver"]],
                "ground_items": sorted(ground)}

    def _resolve_area(self, area: str) -> str:
        locations = self.wiki.data("locations")
        exact = [row["scenename"] for name, row in locations.items()
                 if tokens(name) == tokens(area) and row.get("scenename")]
        if exact:
            return exact[0]
        scene = next((row["scenename"] for row in locations.values()
                      if row.get("scenename", "").casefold() == area.casefold()), None)
        if scene:
            return scene
        matches = {row["scenename"] for name, row in locations.items()
                   if area.casefold() in name.casefold() and row.get("scenename")}
        if len(matches) == 1:
            return matches.pop()
        raise ValueError(f"Unknown or ambiguous area {area!r}; search locations by name first")

    def search(self, query: str, kind: str = "all", limit: int = 10, offset: int = 0,
               details: bool = False, include_stash: bool = False, area: str | None = None,
               ref: SaveRef = SaveRef()) -> dict:
        metadata = {}
        if area and kind == "inventory":
            raise ValueError("area filters wiki locations or merchants, not item storage; omit area for inventory")
        if kind == "inventory":
            inv = self.inventory(ref)
            live = self._live() if str(ref.snapshot) == "0" else None
            live = live if live and live.get("character", "").casefold() == inv["character"].casefold() else None
            metadata = {"character": inv["character"], "snapshot": Path(inv["save"]).name,
                        "source": "live" if live else "save", "silver": (live or inv)["silver"],
                        "stashed_silver": inv["stashed_silver"]}
            metadata["expiry_basis"] = "estimated in-game days at base decay; preservation modifiers not included"
            if live:
                metadata["age_seconds"] = live["age_seconds"]
                metadata["stashed_silver_source"] = "save"
            groups, equipped = {}, set()
            for row in (live or inv)["items"]:
                location = row["location"]
                if location not in LOCATIONS or location == "skill" or (location == "stash" and not include_stash):
                    continue
                item_facts = self.items.by_name(row["name"])
                freshness = _freshness(row, item_facts)
                key = (row["name"], location, row.get("durability") if freshness else None)
                if location == "equipped":
                    identity = (row.get("id"), row["name"])
                    if identity in equipped:
                        continue
                    equipped.add(identity)
                facts = groups.setdefault(key, {"location": location, "qty": 0,
                                               **{k: v for k, v in self.items.by_name(row["name"]).items()
                                                  if k in ("type", "effects", "sell", "weight", "perish_depletion_rate")}})
                facts.update(freshness)
                if row["name"] == WATER_CONTAINER and not live:
                    facts["qty"] += 1
                    facts["water_sips"] = facts.get("water_sips", 0) + row["qty"]
                else:
                    facts["qty"] += row["qty"]
                if isinstance(row.get("durability"), (int, float)):
                    facts["lowest_durability"] = min(facts.get("lowest_durability", float("inf")), row["durability"])
            documents = [{"kind": kind, "name": name, "facts": facts}
                         for (name, location, durability), facts in groups.items()]
            index = SearchIndex(documents)
        elif kind == "merchants":
            documents = []
            for merchant in self.merchants(area, ref):
                for stock in merchant["stock"]:
                    documents.append({"kind": kind, "name": stock["name"],
                                      "facts": {**stock, "merchant": merchant["merchant"],
                                                "area": self._place(merchant["area"])[0]}})
            metadata = {"source": "save", "stock_as_of": "last visit"}
            index = SearchIndex(documents)
        else:
            items, recipes = self.items.all(), self.recipes.recipes
            sources = (items, recipes)
            if self._search_index is None or any(a is not b for a, b in zip(sources, self._search_sources)):
                documents = [{"kind": "items", "name": row["name"], "facts": without_ids(row)}
                             for row in items.values() if row.get("name") and row.get("type") != "Quest"]
                documents += [{"kind": "recipes", "name": row["result"], "facts": row}
                              for row in recipes if self._valid_recipe(row)]
                documents += [{"kind": category, "name": name, "facts": without_ids(row)}
                              for category in self.wiki.KINDS for name, row in self.wiki.data(category).items()]
                self._search_index = SearchIndex(documents)
                self._search_sources = sources
            index = self._search_index
        eligible = None
        if area and kind != "merchants":
            scene = self._resolve_area(area)
            place, _ = self._place(scene)
            aliases = [place]
            eligible = {n for n, doc in enumerate(index.documents)
                        if any(name.casefold() in words(doc).casefold() for name in aliases or [place])}
        matches, total = index.search(query, kind, limit, offset, eligible)
        results = []
        for doc, score in matches:
            facts = without_ids(doc["facts"])
            if doc["kind"] == "enemies":
                facts["weakness"] = _weakness(facts)
            if doc["kind"] == "quests":
                if "quest_status" not in metadata:
                    metadata["quest_status"] = self._quest_status(ref)
                facts["status"] = metadata["quest_status"].get(doc["name"], "not started")
            if not details:
                facts = {k: v for k, v in facts.items() if k in {
                    "summary", "description", "type", "effects", "station", "ingredients", "count",
                    "source", "buy", "sell", "weight", "health", "locations", "location", "region",
                    "requires", "items", "questgiver", "reward", "trainer", "cost", "damage",
                    "damage_resistance", "qty", "water_sips", "lowest_durability",
                    "perish_depletion_rate", "merchant", "area", "weakness", "status",
                    "freshness_percent", "estimated_days_left"}}
                facts = {k: v[:400] if isinstance(v, str) else v for k, v in facts.items()}
            results.append({"kind": doc["kind"], "name": doc["name"], "score": score, "facts": facts})
        metadata.pop("quest_status", None)
        return {"query": query, "kind": kind, **metadata, "total": total, "offset": offset,
                "next_offset": offset + limit if offset + limit < total else None, "results": results}


    # --- items & crafting ---

    def item(self, query: str, limit: int = 10) -> dict:
        return self.items.search(query, limit) or {"error": f"no item matching {query!r}", "items_in_db": len(self.items)}

    def craftable(self, include_stash: bool = False, ref: SaveRef = SaveRef()) -> dict:
        inv = self.inventory(ref)
        stock = Counter()
        for row in inv["items"]:
            if row["location"] == "stash" and not include_stash:
                continue
            stock["Water" if row["name"] == WATER_CONTAINER else row["name"]] += row["qty"]
        craftable = []
        for recipe in self.recipes.recipes:
            if not self._valid_recipe(recipe):
                continue
            times = self._times_craftable(recipe["ingredients"], stock)
            if times:
                craftable.append({"result": recipe["result"], "times": times, "makes": recipe["count"],
                                  "station": recipe["station"],
                                  "station_status": self._station_status(recipe["station"], stock),
                                  "ingredients": self._ingredients(recipe["ingredients"], stock),
                                  "operation": "repair/refill" if recipe["result"] in recipe["ingredients"] else
                                               "dismantle" if any(i.startswith("Decraft") for i in recipe["ingredients"]) else "craft"})
        craftable.sort(key=lambda r: (r["station_status"] != "ok", r["station"], r["result"]))
        return {"save": inv["save"], "snapshot": Path(inv["save"]).name, "character": inv["character"], "source": "save",
                "scope": "carried+stash" if include_stash else "carried",
                "recipes_known": len(self.recipes.recipes), "craftable": craftable}

    def _valid_recipe(self, recipe: dict) -> bool:
        return all(self.items.by_name(i) or self.recipes.groups.get(i) for i in recipe["ingredients"])

    def _ingredients(self, ingredients: list[str], stock: Counter) -> list[str]:
        left, resolved = Counter(stock), []
        for ingredient in sorted(ingredients, key=lambda i: i in self.recipes.groups):
            members = self.recipes.groups.get(ingredient, ())
            pick = next((name for name in (ingredient, *members) if left[name] > 0), None)
            resolved.append(pick or ("Any " + ingredient if members else ingredient))
            if pick:
                left[pick] -= 1
        return resolved

    def _times_craftable(self, ingredients: list[str], stock: Counter) -> int:
        groups = self.recipes.groups
        left = Counter(stock)
        order = sorted(ingredients, key=lambda ing: ing in groups)  # exact names before "any X" groups
        for times in range(MAX_CRAFTS):
            for ingredient in order:
                pick = next((x for x in (ingredient, *groups.get(ingredient, ())) if left[x] > 0), None)
                if pick is None:
                    return times
                left[pick] -= 1
        return MAX_CRAFTS

    @staticmethod
    def _station_status(station: str, stock: Counter) -> str:
        if station in NO_STATION:
            return "ok"
        if station == "Campfire":
            return "need fire"
        needed = STATION_ITEM.get(station)
        if not needed:
            return station
        return "ok" if stock[needed] else f"need {needed}"

    # --- guide: joins game, save and wiki data into answers ---

    def _live(self) -> dict | None:
        live = self.live.read()
        return live if live and live.get("age_seconds", 1e9) <= LIVE_FRESH_S else None

    def _place(self, scene: str) -> tuple[str, str]:
        """Scene name (save/live) -> (wiki location title, region)."""
        locs = self.wiki.data("locations")
        title = next((t for t, l in locs.items() if l.get("scenename") == scene), scene)
        loc = locs.get(title, {})
        return title, loc.get("region") or (title if loc.get("type") == "Region" else "")

    def _quest_status(self, ref: SaveRef) -> dict[str, str]:
        return {q["name"]: q["status"] for q in self.world(ref)["quests"]}

    def area_guide(self, area: str | None = None, ref: SaveRef = SaveRef(), details: bool = False) -> dict:
        live = self._live()
        if live and (str(ref.snapshot) != "0" or
                     live.get("character", "").casefold() != self.saves.character_name(self._snap(ref)).casefold()):
            live = None
        scene = self._resolve_area(area) if area else (live or {}).get("area") or tag(
            self.saves.read(self._snap(ref), CHAR_FILE), "AreaName")
        place, region = self._place(scene)
        locs = self.wiki.data("locations")
        nearby_places = sorted(t for t, l in locs.items() if region and l.get("region") == region)
        names = {n.lower() for n in (place, region) if n}

        def here(text: str | None) -> bool:
            return bool(text) and any(n in text.lower() for n in names)

        status = self._quest_status(ref)
        cities = {t.lower() for t in nearby_places if locs[t].get("type") == "City"}
        quests = [{"quest": t, "status": status.get(t, "not started"), **_pick(q, "type", "questgiver", "location", "reward")}
                  for t, q in self.wiki.data("quests").items()
                  if here(q.get("location")) or any(c in (q.get("location") or "").lower() for c in cities)]
        enemies = [{"enemy": t, **_pick(e, "health", "locations"), "weakness": _weakness(e)}
                   for t, e in self.wiki.data("enemies").items() if here(e.get("locations"))]
        npcs = [{"npc": t, **_pick(n, "type", "teaches", "quests", "locations")}
                for t, n in self.wiki.data("npcs").items() if here(n.get("locations"))]
        gathering = [{"node": t, **_pick(g, "items", "requires")} for t, g in self.wiki.data("gathering").items()
                     if here(g.get("regions")) or g.get("regions") == "All"]
        try:
            loot = self.area_loot(scene, ref)
        except (FileNotFoundError, IndexError):
            loot = {"error": "No saved loot data for this area"}
        result = {"scene": scene, "location": place, "region": region,
                "about": locs.get(place, {}).get("summary"), "places_in_region": nearby_places,
                "quests": sorted(quests, key=lambda q: (q["status"] == "done", q["quest"])),
                "enemies": enemies, "npcs": npcs, "gathering": gathering,
                "merchants": [m["merchant"] for m in self.merchants(scene, ref)],
                "chests_with_loot": without_ids(loot.get("chests_with_loot", []))}
        if details:
            result["loot"] = {**without_ids(loot), "source": "save"}
            result["merchant_stock"] = {"source": "save", "stock_as_of": "last visit",
                                        "merchants": self.merchants(scene, ref)}
        return result

    def nearby_enemies(self) -> dict:
        live = self.live.read()
        if not live or "nearby" not in live:
            return {"error": "No scan data. Rebuild the mod (python mod/build.py) and play; live.json lists nearby enemies."}
        enemies = self.wiki.data("enemies")
        rows = []
        for e in live["nearby"]:
            facts = enemies.get(e["name"]) or {}
            rows.append({**e, "weakness": _weakness(facts),
                         **_pick(facts, "physresist", "ethresist", "decayresist", "lightresist", "frostresist",
                                 "fireresist", "basedamage", "inflicts", "statusimmunity")})
        return {"age_seconds": live.get("age_seconds"), "area": live.get("area"), "enemies": rows}

    def lookup(self, kind: str, query: str, ref: SaveRef = SaveRef()) -> dict:
        hits = self.wiki.find(kind, query)
        if not hits:
            return {"error": f"no {kind} matching {query!r}", "kinds": list(self.wiki.KINDS)}
        if kind == "quests":
            status = self._quest_status(ref)
            hits = {t: {**q, "status": status.get(t, "not started")} for t, q in hits.items()}
        if kind == "enemies":
            hits = {t: {**e, "weakness": _weakness(e)} for t, e in hits.items()}
        return hits

    def craft_advice(self, include_stash: bool = False, ref: SaveRef = SaveRef()) -> dict:
        """Craftable and 1-ingredient-short recipes, upgrades over your gear first, then by value gained."""
        inv = self.inventory(ref)
        stock = Counter()
        for row in inv["items"]:
            if row["location"] != "stash" or include_stash:
                stock["Water" if row["name"] == WATER_CONTAINER else row["name"]] += row["qty"]
        worn = {r.get("equip_slot"): r for r in inv["items"] if r["location"] == "equipped"}
        rows = []
        for recipe in self.recipes.recipes:
            if (not self._valid_recipe(recipe) or recipe["result"] in recipe["ingredients"]
                    or any(i.startswith("Decraft") for i in recipe["ingredients"])):
                continue
            missing = self._missing(recipe["ingredients"], stock)
            if len(missing) > 1 or len(missing) == len(recipe["ingredients"]):  # need at least one in hand
                continue
            facts = self.items.by_name(recipe["result"])
            ingredients = self._ingredients(recipe["ingredients"], stock)
            if recipe["result"] in ingredients:
                continue
            gain = _num(facts.get("value")) * int(recipe["count"]) - sum(self._value(i) for i in recipe["ingredients"])
            row = {"result": recipe["result"], "makes": int(recipe["count"]), "station": recipe["station"],
                   "station_status": self._station_status(recipe["station"], stock),
                   "ingredients": ingredients, "value_gain": round(gain, 1)}
            if missing:
                ingredient = missing[0]
                row["missing"] = "Any " + ingredient if ingredient in self.recipes.groups else ingredient
            blocked_offhand = facts.get("equip_slot") == "LeftHand" and worn.get("RightHand", {}).get("two_handed")
            if not blocked_offhand and (upgrade := _upgrade(facts, worn.get(facts.get("equip_slot")))):
                row["upgrade"] = upgrade
            if blocked_offhand:
                row["requires_equipment_change"] = "Unequip two-handed weapon to use off-hand equipment"
            row.update(_pick(facts, "effects", "type"))
            rows.append(row)
        rows.sort(key=lambda r: ("missing" in r, "upgrade" not in r, -r["value_gain"]))
        return {"source": "save", "snapshot": Path(inv["save"]).name, "character": inv["character"],
                "recipes_source": self.recipes.source, "scope": "carried+stash" if include_stash else "carried",
                "can_craft": [r for r in rows if "missing" not in r],
                "one_ingredient_short": [r for r in rows if "missing" in r][:30]}

    def _missing(self, ingredients: list[str], stock: Counter) -> list[str]:
        left, missing = Counter(stock), []
        for ingredient in sorted(ingredients, key=lambda i: i in self.recipes.groups):
            pick = next((x for x in (ingredient, *self.recipes.groups.get(ingredient, ())) if left[x] > 0), None)
            if pick is None:
                missing.append(ingredient)
            else:
                left[pick] -= 1
        return missing

    def _value(self, ingredient: str) -> float:
        """Base value of an ingredient; for a group ('Meat') the cheapest member."""
        members = self.recipes.groups.get(ingredient) or [ingredient]
        return min(_num(self.items.by_name(m).get("value")) for m in members)

    def guide(self, ref: SaveRef = SaveRef()) -> dict:
        """One call: warnings, what to do here, threats, best crafts."""
        live, inv = self._live(), self.inventory(ref)
        if live and (str(ref.snapshot) != "0" or live.get("character", "").casefold() != inv["character"].casefold()):
            live = None
        warnings = []
        vitals = (live or {}).get("vitals") or {}
        for stat in ("health", "stamina"):
            if vitals.get("max_" + stat) and vitals[stat] / vitals["max_" + stat] < LOW:
                warnings.append(f"low {stat}: {vitals[stat]:.0f}/{vitals['max_' + stat]:.0f}")
        for need in ("Food", "Drink", "Sleep"):
            if inv["vitals"][need] < LOW * NEED_MAX:
                warnings.append(f"low {need.lower()}: {inv['vitals'][need]:.0f}/{NEED_MAX} (save)")
        area = self.area_guide(ref=ref)
        threats = self.nearby_enemies().get("enemies", []) if live else []
        craft = self.craft_advice(ref=ref)
        return {"character": inv["character"], "live": bool(live), "warnings": warnings,
                "sources": {"vitals": "live" if live else "save", "quests": "save", "craft": "save", "loot": "save"},
                "where": {k: area[k] for k in ("location", "region", "about")},
                "active_quests": [{"quest": n, "status": s} for n, s in self._quest_status(ref).items() if s == "active"],
                "quests_to_start_here": [q["quest"] for q in area["quests"] if q["status"] == "not started"],
                "nearby_enemies": threats[:5],
                "best_crafts": craft["can_craft"][:5], "almost_craftable": craft["one_ingredient_short"][:5],
                "chests_with_loot": len(area["chests_with_loot"]), "merchants_here": area["merchants"]}


RESISTS = {"physresist": "Physical", "ethresist": "Ethereal", "decayresist": "Decay",
           "lightresist": "Lightning", "frostresist": "Frost", "fireresist": "Fire"}


def _freshness(row: dict, facts: dict) -> dict:
    rate = facts.get("perish_depletion_rate")
    durability = row.get("durability")
    maximum = row.get("max_durability", facts.get("max_durability"))
    if facts.get("type") != "Food" and not {"Food", "Drink"}.intersection(facts.get("tags", [])):
        return {}
    if not isinstance(rate, (int, float)) or rate <= 0 or not isinstance(durability, (int, float)):
        return {}
    # ponytail: base-decay days only (matches wiki Bread/Meat Stew); export preservation modifiers for effective expiry.
    result = {"estimated_days_left": round(max(0, durability) / rate / 24, 3)}
    if isinstance(maximum, (int, float)) and maximum > 0:
        result["freshness_percent"] = round(max(0, min(100, durability / maximum * 100)), 1)
    return result


def _num(v) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", str(v or ""))
    return float(match.group()) if match else 0.0


def _pick(d: dict, *keys: str) -> dict:
    return {k: d[k] for k in keys if d.get(k)}


def _weakness(enemy: dict) -> str | None:
    """Lowest resistance, the damage type to use. None without wiki data."""
    resists = {name: _num(enemy[k]) for k, name in RESISTS.items() if k in enemy}
    if not resists:
        return None
    low = min(resists.values())
    return ", ".join(f"{n} ({v:g}%)" for n, v in resists.items() if v == low)


def _upgrade(new: dict, worn: dict | None) -> str | None:
    """'+8 damage, +3 resistance vs Machete' when the crafted gear beats what that slot holds."""
    if not new.get("equip_slot"):
        return None
    worn = worn or {"name": "empty slot"}
    gains = []
    for key, label in (("damage", "damage"), ("damage_resistance", "resistance"), ("damage_protection", "protection")):
        diff = sum((new.get(key) or {}).values()) - sum((worn.get(key) or {}).values())
        if diff > 0:
            gains.append(f"+{diff:g} {label}")
    return f"{', '.join(gains)} vs {worn['name']}" if gains else None
