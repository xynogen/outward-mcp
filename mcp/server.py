"""Outward MCP server (stdio JSON-RPC). Offline and read-only: never writes saves, never uses the network."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Callable

from outward.config import DATA_DIR, Paths
from outward.repositories import ItemRepository, LiveRepository, RecipeRepository, SaveRepository, WikiRepository
from outward.services import LOCATIONS, OutwardService, SaveRef
from outward.search import without_ids

PROTOCOL_VERSION = "2024-11-05"
SAVE = {  # shared by every tool that reads a save
    "character": {"type": ["integer", "string"],
                  "description": "Character name, e.g. Xynogen. Default: most recently played. "
                                 "Character numbers and slot UIDs are also accepted."},
    "snapshot": {"type": ["integer", "string"], "default": 0,
                 "description": "0 = latest save, 1 = the one before, ...; or a snapshot id from "
                                "read_save(mode=snapshots) (e.g. '20261025231406')."},
}
PAGING = {"offset": {"type": "integer", "default": 0, "minimum": 0},
          "limit": {"type": "integer", "default": 100, "minimum": 1}}
PUBLIC_TOOLS = {"search", "get_state", "read_save", "get_guide", "get_area_guide", "get_craft_advice"}


def save_ref(args: dict) -> SaveRef:
    return SaveRef(args.get("character"), args.get("snapshot", 0))


def build_service(paths: Paths | None = None) -> OutwardService:
    paths = paths or Paths.discover()
    items = ItemRepository(paths.mod_export / "items.json", DATA_DIR / "wiki_items.json", DATA_DIR / "wiki_names.json")
    return OutwardService(
        saves=SaveRepository(paths.saves),
        items=items,
        recipes=RecipeRepository(DATA_DIR / "wiki_recipes.json", paths.mod_export / "recipes.json", items),
        live=LiveRepository(paths.mod_export / "live.json"),
        wiki=WikiRepository(DATA_DIR),
    )


@dataclass(frozen=True)
class Tool:
    description: str
    handler: Callable[[dict], object]
    properties: dict = field(default_factory=dict)
    required: tuple[str, ...] = ()

    def call(self, args: dict):
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        missing = [key for key in self.required if key not in args]
        if missing:
            raise ValueError("Missing required parameters: " + ", ".join(missing))
        unknown = set(args) - self.properties.keys()
        if unknown:
            raise ValueError("Unknown parameters: " + ", ".join(sorted(unknown)))
        for key, value in args.items():
            schema = self.properties[key]
            types = schema.get("type", [])
            types = [types] if isinstance(types, str) else types
            actual = {str: "string", int: "integer", bool: "boolean", list: "array"}.get(type(value))
            if types and actual not in types:
                raise ValueError(f"{key} must be {' or '.join(types)}")
            if "enum" in schema and value not in schema["enum"]:
                raise ValueError(f"{key} must be one of {schema['enum']}")
            if isinstance(value, list) and any(v not in schema.get("items", {}).get("enum", value) for v in value):
                raise ValueError(f"Invalid {key}; expected {schema['items']}")
            if isinstance(value, str) and len(value.strip()) < schema.get("minLength", 0):
                raise ValueError(f"{key} must not be empty")
            if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
                raise ValueError(f"{key} is too long")
            if type(value) is int and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
                raise ValueError(f"{key} is outside the allowed range")
        return self.handler(args)

    def schema(self, name: str) -> dict:
        input_schema = {"type": "object", "properties": self.properties, "additionalProperties": False}
        if self.required:
            input_schema["required"] = list(self.required)
        return {"name": name, "description": self.description, "inputSchema": input_schema}


def read_save(svc: OutwardService, args: dict):
    mode = args["mode"]
    if mode != "file" and ("file" in args or "section" in args):
        raise ValueError("file and section are only valid with mode=file")
    if mode == "characters":
        return svc.characters()
    if mode == "snapshots":
        return svc.snapshot_list(save_ref(args), args.get("limit", 20))
    if mode == "files":
        return svc.save_files(save_ref(args))
    if "file" not in args:
        raise ValueError("mode=file requires file; use mode=files to list names")
    return svc.read_save_file(args["file"], args.get("section"), save_ref(args),
                              args.get("offset", 0), args.get("limit", 20))


def build_tools(svc: OutwardService) -> dict[str, Tool]:
    return {
        "get_state": Tool(
            "Character state by name: auto uses fresh matching live data, else save. Default vitals only. "
            "sections: inventory (paged per item with expiry), world (save quests/day/season), skills (learned, save), nearby (live enemies). "
            "Historical snapshots never mix with live. Expiry is a base-decay estimate, not a guaranteed timer.",
            lambda a: svc.state(save_ref(a), a.get("sections", ["vitals"]), a.get("source", "auto"),
                                a.get("include_stash", False), a.get("offset", 0), a.get("limit", 20)),
            {**SAVE, "sections": {"type": "array", "items": {"enum": ["vitals", "inventory", "world", "skills", "nearby"]}},
             "source": {"type": "string", "enum": ["auto", "live", "save"], "default": "auto"},
             "include_stash": {"type": "boolean", "default": False},
             "offset": {"type": "integer", "minimum": 0, "default": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}),
        "read_save": Tool(
            "Read-only save navigation. mode=characters lists names; snapshots lists history; files lists sections; "
            "file reads a named file/section with pagination. Use get_state for normal gameplay queries.",
            lambda a: read_save(svc, a),
            {**SAVE, "mode": {"type": "string", "enum": ["characters", "snapshots", "files", "file"]},
             "file": {"type": "string", "minLength": 1}, "section": {"type": "string", "minLength": 1},
             "offset": {"type": "integer", "minimum": 0, "default": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}, ("mode",)),
        "search": Tool(
            "START HERE: BM25 search by name or keywords, no IDs needed. Find item stats and prices, "
            "crafting recipes and ingredients, enemy weaknesses, dungeon locations, quests, trainers, "
            "mana effects, your inventory/stash or merchant stock. Bounded, paged results; details for full facts. "
            "English game keywords work best (e.g. 'burnt mana', 'rag', 'Scaled Leather', 'Conflux'). "
            "Use kind=inventory for owned items (empty query lists them), kind=merchants for shopping, "
            "kind=recipes for recipes using or making an item. Inventory uses fresh matching live data, else saves. "
            "all searches the static catalog; inventory and merchant stock require their explicit kind. "
            "Quest hits include saved player status. Use get_state sections=world for all active quests.",
            lambda a: svc.search(a["query"], a.get("kind", "all"), a.get("limit", 10), a.get("offset", 0),
                                 a.get("details", False), a.get("include_stash", False), a.get("area"), save_ref(a)),
            {**SAVE,
             "query": {"type": "string", "maxLength": 500,
                       "description": "Name or English keywords. Empty string browses a category."},
             "kind": {"type": "string", "enum": ["all", "items", "recipes", *WikiRepository.KINDS,
                                                     "inventory", "merchants"], "default": "all"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
             "offset": {"type": "integer", "minimum": 0, "default": 0},
             "details": {"type": "boolean", "default": False},
             "include_stash": {"type": "boolean", "default": False,
                               "description": "Include stored items when kind=inventory."},
             "area": {"type": "string", "minLength": 1,
                      "description": "Optional location name, e.g. Cierzo, Chersonese or Ghost Pass. No scene ID needed."}},
            ("query",)),
        "get_inventory": Tool(
            "Items (location, qty, durability, item stats), vitals, status effects and silver from a save.",
            lambda a: svc.inventory(save_ref(a), a.get("include_skills", False), a.get("locations")),
            {**SAVE,
             "locations": {"type": "array", "items": {"enum": list(LOCATIONS)},
                           "description": "Only these locations. Default: all except skill."},
             "include_skills": {"type": "boolean", "default": False}}),
        "get_live": Tool(
            "Legacy full live export. Pass sections=[] for summary or items/nearby for selected detail. "
            "Use get_state for named characters or historical saves.",
            lambda a: svc.live_state(a.get("sections")),
            {"sections": {"type": "array", "items": {"enum": ["items", "nearby"]}}}),
        "list_characters": Tool("Characters with their number, name, slot UID, snapshot count and latest save.",
                                lambda a: svc.characters()),
        "list_snapshots": Tool(
            "Save snapshots of a character, newest first: snapshot number, id, in-game day, area, health, silver.",
            lambda a: svc.snapshot_list(save_ref(a), a.get("limit", 20)),
            {"character": SAVE["character"], "limit": {**PAGING["limit"], "default": 20}}),
        "list_save_files": Tool("Every file in a save snapshot with its sections and entry counts.",
                                lambda a: svc.save_files(save_ref(a)), SAVE),
        "read_save_file": Tool(
            "Generic parser: any save file (World.defedc, Char0.defedc, <Area>.deenvc) as JSON. "
            "Without section: overview. With section (QuestList, CharList, StatusList...): content, paged.",
            lambda a: svc.read_save_file(a["file"], a.get("section"), save_ref(a),
                                         a.get("offset", 0), a.get("limit", 100)),
            {**SAVE, **PAGING, "file": {"type": "string"}, "section": {"type": "string"}}, ("file",)),
        "get_world": Tool("Day/time, season, quests (active/done), defeats, building resources, visited areas.",
                          lambda a: svc.world(save_ref(a)), SAVE),
        "get_merchants": Tool("Merchant stock (names, buy price) and silver per merchant, as of your last visit.",
                              lambda a: svc.merchants(a.get("area"), save_ref(a)),
                              {**SAVE, "area": {"type": "string", "minLength": 1, "description": "Location name, e.g. Cierzo"}}),
        "get_area_loot": Tool("Chests with remaining loot and loose ground items in a visited area.",
                              lambda a: svc.area_loot(a["area"], save_ref(a)),
                              {**SAVE, "area": {"type": "string", "minLength": 1, "description": "Visited location name, e.g. Ghost Pass"}}, ("area",)),
        "get_craftable": Tool(
            "What you can craft now: ~420 recipes vs carried items (Waterskin sips count as Water). "
            "Shows how many times and whether a station is needed.",
            lambda a: svc.craftable(a.get("include_stash", False), save_ref(a)),
            {**SAVE, "include_stash": {"type": "boolean", "default": False}}),
        "get_guide": Tool(
            "START HERE for 'what should I do?'. One summary: warnings (low health/food/drink/sleep), current place, "
            "active quests, quests to start here, nearby enemies with weakness, best crafts, loot and merchants.",
            lambda a: svc.guide(save_ref(a)), SAVE),
        "get_area_guide": Tool(
            "Everything about an area (default: where you are now): region, places, quests with your status, "
            "enemies with weakness, NPCs/trainers, gathering nodes, merchants, chests with loot. "
            "details=true adds complete saved ground/chest loot and merchant stock (last visit).",
            lambda a: svc.area_guide(a.get("area"), save_ref(a), a.get("details", False)),
            {**SAVE, "area": {"type": "string", "minLength": 1, "description": "Location name. Default: current."},
             "details": {"type": "boolean", "default": False}}),
        "get_nearby_enemies": Tool(
            "Enemies around you from the mod's scan (name, distance, compass direction, health, in combat), "
            "with wiki resistances, damage, inflicted effects and weakness.",
            lambda a: svc.nearby_enemies()),
        "get_craft_advice": Tool(
            "What is worth crafting: upgrades over your equipped gear first, then by value gained "
            "(result value minus ingredient value). Also recipes you are one ingredient short of. "
            "mode=all lists all craftable recipes including repair/refill and dismantling; source is save, not live.",
            lambda a: without_ids(svc.craftable(a.get("include_stash", False), save_ref(a))) if a.get("mode") == "all"
                      else svc.craft_advice(a.get("include_stash", False), save_ref(a)),
            {**SAVE, "include_stash": {"type": "boolean", "default": False},
             "mode": {"type": "string", "enum": ["advice", "all"], "default": "advice"}}),
        "lookup": Tool(
            "Offline wiki facts by name: quests (giver, reward, prereq, next, your status), enemies (health, "
            "resistances, weakness), npcs, skills (trainer, price), locations, gathering, effects, traps, spells, buildings.",
            lambda a: svc.lookup(a["kind"], a["query"], save_ref(a)),
            {**SAVE, "kind": {"enum": list(WikiRepository.KINDS)},
             "query": {"type": "string", "description": "Name or part of it"}}, ("kind", "query")),
        "get_item": Tool(
            "Item database by object id (e.g. 2020020) or name. Game data from the mod when available, "
            "else the bundled wiki snapshot: damage, impact, protection, resistances, weight, price, effects.",
            lambda a: svc.item(a["query"], a.get("limit", 10)),
            {"query": {"type": "string", "description": "Object id or (partial) item name"},
             "limit": {**PAGING["limit"], "default": 10}}, ("query",)),
    }


def handle(msg: dict, tools: dict[str, Tool]):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "outward", "version": "2.0"}}
    if method == "tools/list":
        return {"tools": [tool.schema(name) for name, tool in tools.items() if name in PUBLIC_TOOLS]}
    if method == "tools/call":
        try:
            name = params.get("name")
            if name not in tools:
                raise ValueError(f"Unknown tool {name!r}")
            result = tools[name].call(params.get("arguments", {}))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except Exception as e:  # report to the client instead of killing the server
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    if method == "ping":
        return {}
    raise KeyError(method)


def main() -> None:
    tools = build_tools(build_service())
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" not in msg:  # notification
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg, tools)}
        except KeyError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": f"unknown method {e}"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
