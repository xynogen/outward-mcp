"""Use cases behind the MCP tools. Logic only; all data comes through the repositories."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .repositories import (CHAR_FILE, WORLD_FILE, ItemRepository, LiveRepository, RecipeRepository,
                           SaveRepository, WikiRepository, container_uid, sync_items, tag, xml_to_obj)

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

    def live_state(self) -> dict:
        return self.live.read() or {
            "error": "No live data. Install the OutwardDataExport mod (python mod/build.py) and start the game.",
            "expected_file": str(self.live.path)}

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
        found = []
        for area_file in self.saves.areas(snap):
            if area and area.lower() not in area_file.stem.lower():
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
            pouches[uid] = {"area": area, "merchant": who.group(1) if who else path,
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
        area_file = next((f for f in self.saves.areas(snap) if area.lower() in f.stem.lower()), None)
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
            times = self._times_craftable(recipe["ingredients"], stock)
            if times:
                craftable.append({"result": recipe["result"], "times": times, "makes": recipe["count"],
                                  "station": recipe["station"],
                                  "station_status": self._station_status(recipe["station"], stock),
                                  "ingredients": recipe["ingredients"]})
        craftable.sort(key=lambda r: (r["station_status"] != "ok", r["station"], r["result"]))
        return {"save": inv["save"], "scope": "carried+stash" if include_stash else "carried",
                "recipes_known": len(self.recipes.recipes), "craftable": craftable}

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

    def area_guide(self, area: str | None = None, ref: SaveRef = SaveRef()) -> dict:
        live = self._live()
        scene = area or (live or {}).get("area") or tag(self.saves.read(self._snap(ref), CHAR_FILE), "AreaName")
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
            loot = self.area_loot(scene, ref).get("chests_with_loot", [])
        except (FileNotFoundError, IndexError):
            loot = []
        return {"scene": scene, "location": place, "region": region,
                "about": locs.get(place, {}).get("summary"), "places_in_region": nearby_places,
                "quests": sorted(quests, key=lambda q: (q["status"] == "done", q["quest"])),
                "enemies": enemies, "npcs": npcs, "gathering": gathering,
                "merchants": [m["merchant"] for m in self.merchants(scene, ref)], "chests_with_loot": loot}

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
            missing = self._missing(recipe["ingredients"], stock)
            if len(missing) > 1 or len(missing) == len(recipe["ingredients"]):  # need at least one in hand
                continue
            facts = self.items.by_name(recipe["result"])
            gain = _num(facts.get("value")) * int(recipe["count"]) - sum(self._value(i) for i in recipe["ingredients"])
            row = {"result": recipe["result"], "makes": int(recipe["count"]), "station": recipe["station"],
                   "station_status": self._station_status(recipe["station"], stock),
                   "ingredients": recipe["ingredients"], "value_gain": round(gain, 1)}
            if missing:
                row["missing"] = missing[0]
            if upgrade := _upgrade(facts, worn.get(facts.get("equip_slot"))):
                row["upgrade"] = upgrade
            row.update(_pick(facts, "effects", "type"))
            rows.append(row)
        rows.sort(key=lambda r: ("missing" in r, "upgrade" not in r, -r["value_gain"]))
        return {"recipes_source": self.recipes.source, "scope": "carried+stash" if include_stash else "carried",
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
                "where": {k: area[k] for k in ("location", "region", "about")},
                "active_quests": [q for q in area["quests"] if q["status"] == "active"] or
                                 [{"quest": n, "status": s} for n, s in self._quest_status(ref).items() if s == "active"],
                "quests_to_start_here": [q["quest"] for q in area["quests"] if q["status"] == "not started"],
                "nearby_enemies": threats[:5],
                "best_crafts": craft["can_craft"][:5], "almost_craftable": craft["one_ingredient_short"][:5],
                "chests_with_loot": len(area["chests_with_loot"]), "merchants_here": area["merchants"]}


RESISTS = {"physresist": "Physical", "ethresist": "Ethereal", "decayresist": "Decay",
           "lightresist": "Lightning", "frostresist": "Frost", "fireresist": "Fire"}


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
