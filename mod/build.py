"""Compile the BepInEx plugin against the installed game and install it: python mod/build.py
--check: compile and run the self-check of the game-independent code (no game needed) instead of installing.
Standalone: no dependency on the MCP server code. OUTWARD_DIR overrides the Steam lookup."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME_IN_LIBRARY = Path("steamapps", "common", "Outward", "Outward_Defed")
# .NET Framework's compiler ships with Windows; it only supports C# 5, which the plugin sticks to.
CSC = Path(os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")
GAME_REFERENCES = ("mscorlib", "System", "System.Core", "netstandard",
                   "UnityEngine", "UnityEngine.CoreModule", "Assembly-CSharp")


def find_game() -> Path:
    if env := os.environ.get("OUTWARD_DIR"):
        return Path(env)
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
        steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    libraries = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="ignore")
        libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    for library in libraries:
        if (library / GAME_IN_LIBRARY).is_dir():
            return library / GAME_IN_LIBRARY
    sys.exit("Outward not found via Steam. Set OUTWARD_DIR to the Outward_Defed folder.")


def compile_(out: Path, sources: list[Path], *flags: str, refs: tuple[str, ...] = ()) -> None:
    result = subprocess.run([str(CSC), "-nologo", "-warnaserror+", *flags, f"-out:{out}", *refs, *map(str, sources)],
                            capture_output=True, text=True)
    if result.returncode:
        sys.exit(result.stdout)


def check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "ModCheck.exe"
        check_src = Path(tmp) / "ModCheck.cs"
        shutil.copy(HERE / "ModCheck.cs.txt", check_src)  # .txt: keeps it out of the plugin build
        compile_(exe, [HERE / "Json.cs", HERE / "Placeholders.cs", check_src], "-target:exe")
        subprocess.run([str(exe)], check=True)


def main() -> None:
    if "--check" in sys.argv:
        return check()
    game = find_game()
    managed = next(game.glob("*_Data/Managed"))
    bepinex = game / "BepInEx"
    dll = HERE / "OutwardDataExport.dll"
    refs = tuple(f"-r:{managed / f'{name}.dll'}" for name in GAME_REFERENCES)
    refs += (f"-r:{bepinex / 'core' / 'BepInEx.dll'}",)
    compile_(dll, sorted(HERE.glob("*.cs")), "-noconfig", "-nostdlib", "-target:library", refs=refs)
    target = bepinex / "plugins" / "OutwardDataExport"
    target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(dll, target)
    except OSError:  # WinError 1224/32: the running game maps the old DLL
        sys.exit(f"built {dll}, but the game has the old one loaded. Close Outward, then run this again.")
    print(f"installed {target / dll.name} (restart the game to load it)")


if __name__ == "__main__":
    main()
