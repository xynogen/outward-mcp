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
from outward.search import SearchIndex, tokens, without_ids  # noqa: E402
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
ranked = [-r["value_gain"] for r in advice["can_craft"]]
assert ranked == sorted(ranked), "craft advice not sorted"
assert all(len(r["ingredients"]) > 1 for r in advice["one_ingredient_short"])
assert svc.lookup("quests", "Castaway")["Castaway"]["next"] == "Call to Adventure"
assert "error" in svc.lookup("enemies", "zzz-no-such-enemy")

tools = build_tools(svc)
listed = handle({"method": "tools/list"}, tools)["tools"]
assert {t["name"] for t in listed} == {
    "search", "get_state", "get_item", "read_save", "get_guide", "get_craft_advice"}
assert len(listed) == 6 and len(tools) == 18
assert "get_area_guide" not in tools
assert all(t == tools[t["name"]].schema(t["name"]) for t in listed)
reply = handle({"method": "tools/call", "params": {"name": "get_world", "arguments": {}}}, tools)
assert not reply.get("isError"), reply

# BM25: punctuation, OR matches, exact-name priority, paging and empty corpora.
assert tokens("Dreamer’s Root") == tokens("DREAMER'S ROOT")
index = SearchIndex([
    {"kind": "items", "name": "Fire Rag", "facts": {"effects": "Fire Imbue"}},
    {"kind": "items", "name": "Other", "facts": {"effects": "Fire Rag " * 10}},
    {"kind": "recipes", "name": "Ice Rag", "facts": {"ingredients": ["Linen Cloth", "Seaweed"]}},
])
assert index.search("Fire Rag")[0][0][0]["name"] == "Fire Rag"
assert index.search("rag fire")[1] == 3
assert index.search("Fire Rag", kind="recipes")[0][0][0]["name"] == "Ice Rag"
assert index.search("zzzzzzz")[1] == 0
assert index.search("", limit=1, offset=1)[0][0][0]["name"] == "Ice Rag"
assert SearchIndex([]).search("mana") == ([], 0)
assert without_ids({"id": 1, "nested": [{"objectid": 2, "name": "x"}]}) == {"nested": [{"name": "x"}]}

# Real catalog: name-only results, cache reuse and ingredients searchable across recipes.
hits = svc.search("Brutal Club", kind="items")
assert hits["results"][0]["name"] == "Brutal Club", hits
cached_index = svc._search_index
assert svc.search("burnt mana", kind="items")["total"] > 0
assert svc._search_index is cached_index
recipes = svc.search("Seaweed", kind="recipes", limit=50)["results"]
assert any(r["name"] == "Ice Rag" for r in recipes), recipes
assert all(r["kind"] == "recipes" for r in recipes)
assert all("id" not in r["facts"] and "objectid" not in r["facts"] for r in hits["results"])
assert svc.search("Bandit", kind="enemies")["results"][0]["facts"]["weakness"]
assert svc.search("", kind="items", limit=1)["next_offset"] == 1
assert not svc.search("zzzznosuchresult", kind="all")["results"]
assert svc.area_guide("Chersonese")["scene"] == "ChersoneseNewTerrain"
assert svc.area_loot("Cierzo")["area"] == "CierzoNewTerrain"
try:
    svc._resolve_area("Conflux")
    raise AssertionError("accepted ambiguous area")
except ValueError:
    pass
owned = svc.search("", kind="inventory", include_stash=True, limit=50)
assert owned["character"] and owned["source"] in ("live", "save")
assert all("location" in r["facts"] for r in owned["results"])
assert all(r["facts"]["location"] != "stash" for r in svc.search("", kind="inventory", limit=50)["results"])
assert svc.search("", kind="inventory", ref=SaveRef(0, snaps[0]["id"]))["source"] == "save"
shopping = svc.search("Life Potion", kind="merchants", area="Cierzo")
assert shopping["source"] == "save" and all(r["facts"]["area"] == "Cierzo" for r in shopping["results"])

from unittest.mock import patch  # noqa: E402
fake_inv = {"character": "Test", "save": "saves/snapshot", "silver": 10, "stashed_silver": 20,
            "items": [{"name": "Waterskin", "location": "pouch", "qty": 0}]}
fake_live = {"character": "Test", "age_seconds": 1, "silver": 11, "items": [
    {"id": 1, "name": "Fang Halberd", "location": "equipped", "qty": 1},
    {"id": 1, "name": "Fang Halberd", "location": "equipped", "qty": 1},
    {"id": 2, "name": "Arrow", "location": "equipped", "qty": 15},
    {"id": 3, "name": "Waterskin", "location": "pouch", "qty": 1}]}
with patch.object(svc, "inventory", return_value=fake_inv), patch.object(svc, "_live", return_value=fake_live):
    found = svc.search("", kind="inventory")
    quantities = {r["name"]: r["facts"]["qty"] for r in found["results"]}
    assert found["source"] == "live" and found["silver"] == 11
    assert quantities["Fang Halberd"] == 1 and quantities["Arrow"] == 15
    fake_live["character"] = "Other"
    found = svc.search("", kind="inventory")
    assert found["source"] == "save" and found["results"][0]["facts"]["water_sips"] == 0
    assert found["results"][0]["facts"]["qty"] == 1
    fake_live["character"] = "Test"
    assert svc.search("", kind="inventory", ref=SaveRef(0, "older"))["source"] == "save"
    assert svc.search("zzzznosuchresult", kind="inventory")["total"] == 0

# Every advertised input boundary rejects bad values before invoking the handler.
for arguments in ({}, {"query": 1}, {"query": None}, {"query": "x", "limit": 0},
                  {"query": "x", "limit": True}, {"query": "x", "limit": 51},
                  {"query": "x", "offset": -1}, {"query": "x", "kind": "unknown"},
                  {"query": "x", "area": " "}, {"query": "x", "unexpected": True}):
    reply = handle({"method": "tools/call", "params": {"name": "search", "arguments": arguments}}, tools)
    assert reply.get("isError"), arguments
reply = handle({"method": "tools/call", "params": {"name": "search",
               "arguments": {"query": "Ice Rag", "kind": "recipes", "limit": 1}}}, tools)
assert not reply.get("isError"), reply
# Public tools retain state, exhaustive crafts, detailed loot, and raw save navigation.
import json  # noqa: E402
from outward.services import _compare, _freshness  # noqa: E402

def call(name, **arguments):
    reply = handle({"method": "tools/call", "params": {"name": name, "arguments": arguments}}, tools)
    assert not reply.get("isError"), reply
    return json.loads(reply["content"][0]["text"])

assert call("read_save", mode="characters") == chars
assert call("read_save", mode="snapshots", character=first["name"], limit=1)["snapshots"][0]["id"] == first["latest"]
assert call("read_save", mode="files")["files"]
assert call("read_save", mode="file", file="World.defedc")
state = call("get_state", character=first["name"], source="save",
             sections=["vitals", "inventory", "world", "nearby"], include_stash=True, limit=2)
assert state["source"] == "save" and state["world"]["day"] >= 1
assert state["status"]["source"] == "save" and state["nearby"]["available"] is False
assert len(state["inventory"]["items"]) == 2 and state["inventory"]["next_offset"] == 2
assert all(set(r) == {"id", "name"} for r in state["inventory"]["items"])
for row in state["inventory"]["items"]:
    assert call("get_item", query=row["id"])[row["id"]]["name"] == row["name"]
skills = call("get_state", source="save", sections=["skills"])["skills"]["items"]
assert skills and all(set(r) == {"id", "name"} for r in skills)
assert all(call("get_item", query=r["id"])[r["id"]]["name"] == r["name"] for r in skills)
detailed = call("get_state", source="save", sections=["inventory"], details=True, limit=2)
assert all("qty" in r and "location" in r for r in detailed["inventory"]["items"])
assert call("get_item", query="2020020")["2020020"]["name"] == "Brutal Club"
assert "error" in call("get_item", query="no-such-item-id")
assert "burnt_stamina" in state["vitals"] and "BurntStamina" not in state["vitals"]
assert call("get_state", source="save", sections=["skills"])["skills"]["source"] == "save"
assert call("get_craft_advice", mode="all", include_stash=True)["craftable"]
assert "ground_items" in call("get_guide", area="Cierzo", details=True)["loot"]
assert call("get_guide")["character"] == first["name"]
assert call("get_guide", area="Chersonese")["scene"] == "ChersoneseNewTerrain"
with patch.object(svc, "guide", return_value={"mode": "player"}) as player_guide, \
        patch.object(svc, "area_guide", return_value={"mode": "area"}) as area_guide:
    assert call("get_guide", character=first["name"], snapshot=snaps[0]["id"])["mode"] == "player"
    player_guide.assert_called_once_with(SaveRef(first["name"], snaps[0]["id"]))
    area_guide.assert_not_called()
    assert call("get_guide", details=True)["mode"] == "area"
    area_guide.assert_called_once_with(None, SaveRef(), True)
    call("get_guide", area="Cierzo", character=first["name"], snapshot=snaps[0]["id"])
    area_guide.assert_called_with("Cierzo", SaveRef(first["name"], snaps[0]["id"]), False)
assert "status" in call("search", query="Castaway", kind="quests")["results"][0]["facts"]
for name, arguments in [("read_save", {"mode": "file"}),
                        ("read_save", {"mode": "characters", "file": "World.defedc"}),
                        ("get_state", {"sections": ["invalid"]}),
                        ("get_state", {"limit": 101}),
                        ("get_state", {"details": "yes"}),
                        ("get_item", {"query": " "}),
                        ("get_item", {"query": 2020020}),
                        ("get_item", {"query": "x", "limit": 0}),
                        ("get_guide", {"area": " "}),
                        ("get_guide", {"area": 1}),
                        ("get_guide", {"area": "Conflux"}),
                        ("get_guide", {"details": "yes"}),
                        ("get_area_guide", {}),
                        ("get_state", {"source": "live", "snapshot": "old"})]:
    assert handle({"method": "tools/call", "params": {"name": name, "arguments": arguments}}, tools).get("isError")

# Base food decay is durability points per game-hour; non-perishable/unknown values have no timer.
assert _freshness({"durability": 24, "max_durability": 100}, {"type": "Food", "perish_depletion_rate": 1}) == {
    "estimated_days_left": 1, "freshness_percent": 24}
assert _freshness({"durability": -1}, {"type": "Food", "perish_depletion_rate": 1})["estimated_days_left"] == 0
assert not _freshness({"durability": 50}, {"type": "Food", "perish_depletion_rate": 0})
assert not _freshness({}, {"type": "Food", "perish_depletion_rate": 1})
assert not _freshness({"durability": 100}, svc.items.by_name("Makeshift Torch"))
# Wiki base shelf lives: Meat Stew 9d22h, Bread ~6d (exported rates are rounded).
for name, hours in (("Meat Stew", 238), ("Bread", 144)):
    facts = svc.items.by_name(name)
    if facts.get("perish_depletion_rate"):
        estimate = _freshness({"durability": 100}, facts)["estimated_days_left"] * 24
        assert abs(estimate - hours) < 2, (name, estimate)
fake_inv.update(area="CierzoNewTerrain", vitals={}, status=[])
fake_live.update(character="Test", area="CierzoNewTerrain", vitals={}, nearby=[])
fake_live["items"] += [{"id": next(iter(svc.item("Meat Stew"))), "name": "Meat Stew", "location": "stash", "qty": 1,
                        "durability": d, "max_durability": 100} for d in (10, 10, 90)]
with patch.object(svc, "inventory", return_value=fake_inv), patch.object(svc, "_live", return_value=fake_live):
    foods = svc.search("Meat Stew", kind="inventory", include_stash=True)["results"]
    assert len(foods) == 2 and sum(r["facts"]["qty"] for r in foods) == 3
    assert {r["facts"]["freshness_percent"] for r in foods} == {10, 90}
    current = svc.state(sections=["inventory"], include_stash=True, limit=1)
    assert set(current["inventory"]["items"][0]) == {"id", "name"}
    assert current["source"] == "live"
    current = svc.state(sections=["inventory"], include_stash=True, limit=1, details=True)
    assert current["inventory"]["items"][0]["freshness_percent"] == 10
    assert current["inventory"]["items"][0]["id"] == next(iter(svc.item("Meat Stew")))
    assert svc.state(ref=SaveRef(0, "old"))["source"] == "save"
    fake_live["character"] = "Other"
    assert svc.state()["source"] == "save"
with patch.object(svc.live, "read", return_value=fake_live):
    assert "items" not in svc.live_state([]) and "nearby" not in svc.live_state([])
    assert svc.live_state() == fake_live  # legacy callers retain the full export
    assert all("id" not in r for r in svc.live_state(["items"])["items"])

# Repair/refill and salvage stay accessible, but are never recommended as new gear.
fake_inv["items"] = [
    {"name": "Fang Halberd", "qty": 1, "location": "equipped", **svc.items.by_name("Fang Halberd")},
    {"name": "Iron Scrap", "qty": 5, "location": "pouch"},
    {"name": "Linen Cloth", "qty": 5, "location": "pouch"},
    {"name": "Cold Stone", "qty": 1, "location": "pouch"}]
with patch.object(svc, "inventory", return_value=fake_inv):
    rows = svc.craft_advice()["can_craft"]
    dagger = next(r for r in rows if r["result"] == "Shiv Dagger")
    assert "stat_comparison" not in dagger and dagger["requires_equipment_change"]
    advice = svc.craft_advice()
    for row in advice["can_craft"] + advice["one_ingredient_short"]:
        assert row["result"] not in row["ingredients"]
        assert not any(i.startswith("Decraft") for i in row["ingredients"])
        assert not row.get("missing", "").startswith("Decraft")
    assert not svc._valid_recipe({"ingredients": ["zzznosuchingredient"]})
    assert svc._valid_recipe({"ingredients": ["Basic Armor"]})  # legitimate ingredient group, not junk
    assert svc._ingredients(["Basic Armor"], {"Makeshift Leather Attire": 1}) == ["Makeshift Leather Attire"]
    fake_inv["items"].append({"name": "Hailfrost Claymore", "qty": 1, "location": "pouch"})
    assert not any(r["result"] == "Hailfrost Claymore" for r in svc.craft_advice()["can_craft"])
    assert any(r["result"] == "Hailfrost Claymore" and r["operation"] == "repair/refill"
               for r in svc.craftable()["craftable"])
    fake_inv["items"].append({"name": "Makeshift Leather Attire", "qty": 1, "location": "pouch"})
    assert all(r["result"] not in r["ingredients"] for r in svc.craft_advice()["can_craft"])
for path in ("5/_SNPC/_UNPC/UNPC_MathiasA (1)/Dialogue_Mathias/NPC/MerchantSettings",
             "5/_SNPC/_UNPC/BlueChamberMeeting/UNPC_MathiasA (2)/Dialogue_Mathias/NPC/MerchantSettings"):
    merchant = svc._area_merchants("Berg", [
        f"<UID>MerchantPouch_test</UID><Hierarchy>{path}</Hierarchy>"
        "<MerchantPouchContainedSilver>MerchantPouchContainedSilver/1</MerchantPouchContainedSilver>"])
    assert len(merchant) == 1 and merchant[0]["merchant"] == "Mathias", merchant
assert all("HumanSNPC_" not in m["merchant"] and "/" not in m["merchant"] for m in svc.merchants())

# Craft advice follows fresh live items (save only for waterskin sips); comparisons are per type, not a verdict.
def item_id(name):
    return next(i for i, f in svc.items.all().items() if f.get("name") == name)

craft_live = {"character": "Test", "age_seconds": 1, "silver": 0, "items": [
    {"id": item_id(n), "name": n, "location": loc, "qty": q} for n, loc, q in (
        ("Horror Axe", "equipped", 1), ("Iron Scrap", "pouch", 5), ("Linen Cloth", "pouch", 5),
        ("Waterskin", "pouch", 1))]}
craft_save = {"character": "Test", "save": "saves/snapshot", "items": [
    {"name": "Gravel Beetle", "qty": 9, "location": "pouch"},
    {"name": "Waterskin", "qty": 3, "location": "pouch"}]}
with patch.object(svc, "inventory", return_value=craft_save), patch.object(svc, "_live", return_value=craft_live):
    advice = svc.craft_advice()
    assert advice["source"] == "live" and advice["water_source"] == "save"
    used = {i for r in advice["can_craft"] for i in r["ingredients"]}
    assert "Gravel Beetle" not in used and "Iron Scrap" in used, used
    assert svc._stock(SaveRef(), False)[2]["Water"] == 3
    assert svc.craftable()["source"] == "live"
    assert svc.craft_advice(ref=SaveRef(0, "older"))["source"] == "save"
    craft_live["character"] = "Other"
    assert svc.craft_advice()["source"] == "save"
worn = {"RightHand": svc.items.by_name("Horror Axe"), "LeftHand": {"name": "Shiv Dagger"}}
halberd = _compare(svc.items.by_name("Horror Halberd"), worn)
assert halberd["vs"] == "Horror Axe" and "Shiv Dagger" in halberd["hands"], halberd
assert set(halberd["damage"]) == {"Physical", "Decay"}
helm = _compare(svc.items.by_name("Ammolite Helm"), {})
assert helm["damage_resistance"] == {"Frost": 15, "Physical": 9} and helm["stamina_use_penalty"] == 2
assert _compare({"name": "Ring"}, {}) is None

# exact=true drops partial matches; curly and straight apostrophes are the same name.
assert {r["name"] for r in svc.search("Mineral Tea", kind="items", exact=True, limit=50)["results"]} == {"Mineral Tea"}
assert svc.search("Mefino's Trade Backpack", kind="items", exact=True)["total"] == 1

# stdin is read as UTF-8, not the Windows code page (Mefino’s became Mefinoâ€™s).
import os, subprocess  # noqa: E402,E401
probe = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search",
                   "arguments": {"query": "Mefino’s Trade Backpack", "kind": "items", "exact": True}}},
                  ensure_ascii=False)
out = subprocess.run([sys.executable, str(Path(__file__).with_name("server.py"))], input=probe.encode(),
                     capture_output=True, env={**os.environ, "PYTHONIOENCODING": "", "PYTHONUTF8": "0"})
assert '"total": 1' in json.loads(out.stdout)["result"]["content"][0]["text"], out
print("ok: compact public surface, state/save access, food expiry, craft repairs and off-hand compatibility")

print("ok: BM25 search, pagination, names, inventory, merchants and parameter validation")

print(f"ok: {len(chars)} characters, {inv['character']} day {world['day']}, {len(inv['items'])} items, {len(files)} save files, "
      f"{len(svc.merchants())} merchants, {len(craft['craftable'])} craftable, {len(svc.items)} items in DB, "
      f"live={'yes' if svc.live.read() else 'no'}")
