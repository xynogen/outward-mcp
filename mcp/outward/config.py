"""Locate the Outward install. OUTWARD_DIR overrides; otherwise Steam's library list is used."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

GAME_IN_LIBRARY = Path("steamapps", "common", "Outward", "Outward_Defed")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _steam_libraries() -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return []
    libraries = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="ignore")
        libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    return libraries


def find_game_dir() -> Path:
    if env := os.environ.get("OUTWARD_DIR"):
        return Path(env)
    for library in _steam_libraries():
        if (library / GAME_IN_LIBRARY).is_dir():
            return library / GAME_IN_LIBRARY
    raise FileNotFoundError("Outward not found via Steam. Set OUTWARD_DIR to the Outward_Defed folder.")


@dataclass(frozen=True)
class Paths:
    game: Path

    @classmethod
    def discover(cls) -> Paths:
        return cls(find_game_dir())

    @property
    def saves(self) -> Path:
        return self.game / "SaveGames"

    @property
    def mod_export(self) -> Path:
        """Folder the OutwardDataExport mod (../mod) writes its JSON to: the only link to the mod."""
        return self.game / "BepInEx" / "outward-mcp"
