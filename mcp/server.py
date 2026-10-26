"""Outward MCP server (stdio JSON-RPC). Offline and read-only: never writes saves, never uses the network."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Callable

from outward.config import DATA_DIR, Paths
from outward.repositories import ItemRepository, LiveRepository, RecipeRepository, SaveRepository, WikiRepository
from outward.services import LOCATIONS, OutwardService, SaveRef

PROTOCOL_VERSION = "2024-11-05"
SAVE = {  # shared by every tool that reads a save
    "character": {"type": ["integer", "string"],
                  "description": "Character number from list_characters (0 = most recently played), "
                                 "or name / slot UID. Default: most recently played."},
    "snapshot": {"type": ["integer", "string"], "default": 0,
                 "description": "0 = latest save, 1 = the one before, ...; or a snapshot id from "
                                "list_snapshots (e.g. '20261025231406')."},
}
PAGING = {"offset": {"type": "integer", "default": 0, "minimum": 0},
          "limit": {"type": "integer", "default": 100, "minimum": 1}}


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

    def schema(self, name: str) -> dict:
        input_schema = {"type": "object", "properties": self.properties}
        if self.required:
            input_schema["required"] = list(self.required)
        return {"name": name, "description": self.description, "inputSchema": input_schema}


def build_tools(svc: OutwardService) -> dict[str, Tool]:
    return {
        "get_inventory": Tool(
            "Items (location, qty, durability, item stats), vitals, status effects and silver from a save.",
            lambda a: svc.inventory(save_ref(a), a.get("include_skills", False), a.get("locations")),
            {**SAVE,
             "locations": {"type": "array", "items": {"enum": list(LOCATIONS)},
                           "description": "Only these locations. Default: all except skill."},
             "include_skills": {"type": "boolean", "default": False}}),
        "get_live": Tool(
            "Real-time state from the OutwardDataExport mod (updates every 15 s while the game runs): "
            "vitals incl. max values, items, silver, area.",
            lambda a: svc.live_state()),
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
                              {**SAVE, "area": {"type": "string", "description": "Filter, e.g. Cierzo"}}),
        "get_area_loot": Tool("Chests with remaining loot and loose ground items in a visited area.",
                              lambda a: svc.area_loot(a["area"], save_ref(a)),
                              {**SAVE, "area": {"type": "string"}}, ("area",)),
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
            "enemies with weakness, NPCs/trainers, gathering nodes, merchants, chests with loot.",
            lambda a: svc.area_guide(a.get("area"), save_ref(a)),
            {**SAVE, "area": {"type": "string", "description": "Scene name, e.g. ChersoneseNewTerrain. Default: current."}}),
        "get_nearby_enemies": Tool(
            "Enemies around you from the mod's scan (name, distance, compass direction, health, in combat), "
            "with wiki resistances, damage, inflicted effects and weakness.",
            lambda a: svc.nearby_enemies()),
        "get_craft_advice": Tool(
            "What is worth crafting: upgrades over your equipped gear first, then by value gained "
            "(result value minus ingredient value). Also recipes you are one ingredient short of.",
            lambda a: svc.craft_advice(a.get("include_stash", False), save_ref(a)),
            {**SAVE, "include_stash": {"type": "boolean", "default": False}}),
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
        return {"tools": [tool.schema(name) for name, tool in tools.items()]}
    if method == "tools/call":
        try:
            result = tools[params["name"]].handler(params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except Exception as e:  # report to the client instead of killing the server
            return {"content": [{"type": "text", "text": f"error: {e!r}"}], "isError": True}
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
