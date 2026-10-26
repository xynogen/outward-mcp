"""Compile the BepInEx plugin against the installed game and install it: python mod/build.py
--check: compile and run the Json self-check (no game needed) instead of installing."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from outward.config import Paths  # noqa: E402

# .NET Framework's compiler ships with Windows; it only supports C# 5, which the plugin sticks to.
CSC = Path(os.environ["WINDIR"], "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")
GAME_REFERENCES = ("mscorlib", "System", "System.Core", "netstandard",
                   "UnityEngine", "UnityEngine.CoreModule", "Assembly-CSharp")


def compile_(out: Path, sources: list[Path], *flags: str, refs: tuple[str, ...] = ()) -> None:
    result = subprocess.run([str(CSC), "-nologo", "-warnaserror+", *flags, f"-out:{out}", *refs, *map(str, sources)],
                            capture_output=True, text=True)
    if result.returncode:
        sys.exit(result.stdout)


def check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "JsonCheck.exe"
        check_src = Path(tmp) / "JsonCheck.cs"
        shutil.copy(HERE / "JsonCheck.cs.txt", check_src)  # .txt: keeps it out of the plugin build
        compile_(exe, [HERE / "Json.cs", check_src], "-target:exe")
        subprocess.run([str(exe)], check=True)


def main() -> None:
    if "--check" in sys.argv:
        return check()
    paths = Paths.discover()
    dll = HERE / "OutwardDataExport.dll"
    refs = tuple(f"-r:{paths.managed / f'{name}.dll'}" for name in GAME_REFERENCES)
    refs += (f"-r:{paths.bepinex / 'core' / 'BepInEx.dll'}",)
    compile_(dll, sorted(HERE.glob("*.cs")), "-noconfig", "-nostdlib", "-target:library", refs=refs)
    target = paths.bepinex / "plugins" / "OutwardDataExport"
    target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(dll, target)
    except OSError:  # WinError 1224/32: the running game maps the old DLL
        sys.exit(f"built {dll}, but the game has the old one loaded. Close Outward, then run this again.")
    print(f"installed {target / dll.name} (restart the game to load it)")


if __name__ == "__main__":
    main()
