"""Snapshot non-item wiki data (quests, places, enemies, NPCs...) into mcp/data/wiki_*.json. Run by hand:
python mcp/data/fetch_wiki.py. Item ids, stats, recipes and statuses come from the game mod instead.
The MCP server never uses the network; it only reads these files."""
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://outward.wiki.gg/api.php?format=json&"  # outward.fandom.com blocks bots
HERE = Path(__file__).resolve().parent
TEMPLATES = {"locations": "Location", "quests": "Infobox quest", "enemies": "Enemy", "npcs": "NPC",
             "skills": "Infobox skill", "gathering": "Infobox gathering", "effects": "Infobox effect",
             "buildings": "Infobox building", "traps": "Infobox Trap", "spells": "Infobox spell"}
DROP = {"name", "image", "size", "caption", "img1", "img1text", "img2", "img2text", "map", "maptext",
        "center", "zoom", "objectname", "marked_name"}


def api(query: str) -> dict:
    request = urllib.request.Request(API + query, headers={"User-Agent": "outward-mcp/1.0 (offline snapshot)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def titles(template: str) -> list[str]:
    out, cont = [], ""
    while True:
        d = api(f"action=query&list=embeddedin&einamespace=0&eilimit=500&eititle=Template:"
                f"{urllib.parse.quote(template)}{cont}")
        out += [p["title"] for p in d["query"]["embeddedin"]]
        if "continue" not in d:
            return out
        cont = "&eicontinue=" + urllib.parse.quote(d["continue"]["eicontinue"])


def pages(names: list[str], prop: str, batch: int) -> dict[str, dict]:
    out = {}
    for i in range(0, len(names), batch):
        d = api(f"action=query&prop={prop}&titles=" + urllib.parse.quote("|".join(names[i:i + batch])))
        out.update({p["title"]: p for p in d["query"]["pages"].values()})
    return out


def clean(v: str) -> str:
    v = re.sub(r"\{\{\s*tick\s*\}\}", "yes", v, flags=re.I)
    v = re.sub(r"\{\{\s*x\s*\}\}", "no", v, flags=re.I)
    v = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", v, flags=re.I)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"<li>|<br\s*/?>|\n\*", "; ", v)
    v = re.sub(r"\{\{[^{}]*\}\}|<[^>]+>|''+", "", v)
    return re.sub(r"\s+", " ", v).strip(" ;,")


def infobox(text: str, template: str) -> dict[str, str]:
    """Fields of {{template | k = v ...}}; brace-matched, so nested templates and same-line '}}' work."""
    m = re.search(r"\{\{\s*" + re.escape(template).replace(r"\ ", r"[ _]") + r"\s*[|\n]", text, re.I)
    if not m:
        return {}
    depth, i, parts, start = 0, m.start(), [], m.end() - 1
    while i < len(text):
        two = text[i:i + 2]
        if two in ("{{", "[["):
            depth, i = depth + 1, i + 2
            continue
        if two in ("}}", "]]"):
            depth, i = depth - 1, i + 2
            if depth == 0:
                parts.append(text[start:i - 2])
                break
            continue
        if text[i] == "|" and depth == 1:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    out = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip().lower().replace(" ", "_"), clean(v)
            if k not in DROP and v:
                out[k] = v
    return out


def main() -> None:
    for name, template in TEMPLATES.items():
        found = titles(template)
        text = {t: p["revisions"][0]["slots"]["main"]["*"]
                for t, p in pages(found, "revisions&rvprop=content&rvslots=main", 50).items() if "revisions" in p}
        intro = {t: p.get("extract", "") for t, p in
                 pages(found, "extracts&exintro=1&explaintext=1&exlimit=20&exchars=400", 20).items()}
        rows = {}
        for title, wikitext in text.items():
            row = infobox(wikitext, template)
            summary = re.search(r"==\s*(?:Quest )?Summary\s*==\s*\n(.*?)(?:\n==|$)", wikitext, re.S)
            if summary:  # quests: the Summary section beats the one-line intro
                row["summary"] = clean(re.sub(r"\{\{quote.*?\}\}", "", summary.group(1), flags=re.S))[:400]
            elif clean(intro.get(title, "")):
                row["summary"] = clean(intro[title])
            rows[title] = row
        path = HERE / f"wiki_{name}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        print(f"{path.name}: {len(rows)}")


if __name__ == "__main__":
    main()
