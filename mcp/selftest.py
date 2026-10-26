"""Smoke test against the real install, with networking disabled: python mcp/selftest.py"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run from any folder


class _NoNetwork(socket.socket):
    def __init__(self, *args, **kwargs):
        raise AssertionError("network access attempted")


socket.socket = _NoNetwork  # before anything else is imported

from outward.services import SaveRef  # noqa: E402
from server import build_service, build_tools, handle  # noqa: E402

svc = build_service()

chars = svc.characters()
assert chars and chars[0]["character"] == 0, chars
first = chars[0]
assert svc.inventory(SaveRef(0))["character"] == first["name"]  # by number
assert svc.inventory(SaveRef(first["name"].upper()))["character"] == first["name"]  # by name
assert svc.inventory(SaveRef(first["uid"]))["character"] == first["name"]  # by slot uid
snaps = svc.snapshot_list(SaveRef(0), limit=3)["snapshots"]
assert snaps[0]["id"] == first["latest"]
if len(snaps) > 1:  # older snapshot by index and by id
    older = svc.inventory(SaveRef(0, 1))["save"]
    assert older.endswith(snaps[1]["id"]) and svc.inventory(SaveRef(0, snaps[1]["id"]))["save"] == older
for bad in (SaveRef(99), SaveRef(0, 999), SaveRef("nobody")):
    try:
        svc.inventory(bad)
        raise AssertionError(f"accepted {bad}")
    except (IndexError, FileNotFoundError):
        pass
only = svc.inventory(locations=["pouch", "equipped"])["items"]
assert only and {r["location"] for r in only} <= {"pouch", "equipped"}

inv = svc.inventory()
assert inv["items"] and inv["character"], inv
assert {r["location"] for r in inv["items"]} <= {"backpack", "pouch", "stash", "equipped"} | {
    r["location"] for r in inv["items"] if r["location"].startswith("other:")}
unknown = [r["id"] for r in inv["items"] if r["name"].startswith("unknown:")]
assert not unknown, unknown

files = svc.save_files()["files"]
assert any(f["file"] == "World.defedc" for f in files)
for f in files:
    svc.read_save_file(f["file"])  # every file must parse
assert "error" in svc.read_save_file("Char0.defedc", "x]|//y")  # XPath input is validated

world = svc.world()
assert world["day"] >= 1 and world["quests"], world

craft = svc.craftable(include_stash=True)
assert craft["recipes_known"] > 300, craft["recipes_known"]

club = svc.items.get("2020020")
assert club["name"] == "Brutal Club" and float(club["impact"]) == 38, club
assert svc.item("Fur Helm") and "error" in svc.item("zzz-no-such-item")

guide = svc.guide()
assert guide["character"] and guide["where"]["region"], guide
area = svc.area_guide("ChersoneseNewTerrain")
assert area["region"] == "Chersonese" and any(q["quest"] == "Call to Adventure" for q in area["quests"]), area
assert any(e["enemy"] == "Bandit" and e["weakness"] for e in area["enemies"])
advice = svc.craft_advice(include_stash=True)
ranked = [("upgrade" not in r, -r["value_gain"]) for r in advice["can_craft"]]
assert ranked == sorted(ranked), "craft advice not sorted"
assert all(len(r["ingredients"]) > 1 for r in advice["one_ingredient_short"])
assert svc.lookup("quests", "Castaway")["Castaway"]["next"] == "Call to Adventure"
assert "error" in svc.lookup("enemies", "zzz-no-such-enemy")

tools = build_tools(svc)
listed = handle({"method": "tools/list"}, tools)["tools"]
assert {t["name"] for t in listed} == set(tools)
reply = handle({"method": "tools/call", "params": {"name": "get_world", "arguments": {}}}, tools)
assert not reply.get("isError"), reply

print(f"ok: {len(chars)} characters, {inv['character']} day {world['day']}, {len(inv['items'])} items, {len(files)} save files, "
      f"{len(svc.merchants())} merchants, {len(craft['craftable'])} craftable, {len(svc.items)} items in DB, "
      f"live={'yes' if svc.live.read() else 'no'}")
