"""Sorter and discovery tests for FlashManager. Run: py -3 test_engine.py"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

import engine


def fail(message: str) -> None:
    raise SystemExit(message)


def game_dir(root: Path) -> Path:
    folder = root / "Red Dead Redemption 2"
    folder.mkdir(parents=True)
    (folder / "RDR2.exe").write_bytes(b"MZ" + b"\x00" * 64)
    return folder


def place(staging: Path, game: Path, **kwargs) -> engine.PlaceResult:
    log = engine.Log()
    return engine.place_tree(staging, game, log, **kwargs)


def write(folder: Path, rel: str, data: bytes = b"data") -> None:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_loose_rules(tmp: Path) -> None:
    game = game_dir(tmp / "loose")
    staging = tmp / "loose-drop"
    write(staging, "Pack/CoolGun.asi", b"asi-bytes")
    write(staging, "Pack/CoolGun.ini", b"ini")
    write(staging, "Pack/Helper.dll", b"dll-next-to-asi")
    write(staging, "Solo/HatScript.dll", b"csharp")
    write(staging, "notes.txt", b"hello")
    write(staging, "coat.ytd", b"texture")
    write(staging, "bink2w64.dll", b"nope")
    result = place(staging, game)
    if not (game / "CoolGun.asi").is_file() or not (game / "CoolGun.ini").is_file():
        fail("an asi plugin and its ini should sit next to RDR2.exe")
    if not (game / "Helper.dll").is_file():
        fail("a dll dropped beside an asi should sit next to RDR2.exe")
    if not (game / "scripts" / "HatScript.dll").is_file():
        fail("a lone dll should go in scripts")
    if (game / "notes.txt").exists():
        fail("readme files should stay out of the game folder")
    if (game / "bink2w64.dll").exists():
        fail("the game's bink dll must not be replaced")
    parked = list((game / "_flashmanager_review").rglob("coat.ytd"))
    if not parked:
        fail("a loose texture without install.xml should be set aside")
    if not result.ok:
        fail("a normal drop should succeed")


def test_lml_mod_and_nested_xml(tmp: Path) -> None:
    game = game_dir(tmp / "mod")
    staging = tmp / "mod-drop"
    write(staging, "Cool Hat/install.xml", b"<easy/>")
    write(staging, "Cool Hat/inner/install.xml", b"<easy/>")
    write(staging, "Cool Hat/coat.ytd", b"tex")
    place(staging, game)
    if not (game / "lml" / "Cool Hat" / "install.xml").is_file():
        fail("a folder with install.xml should go in lml")
    if not (game / "lml" / "Cool Hat" / "coat.ytd").is_file():
        fail("mod files should stay with their install.xml")
    if (game / "lml" / "inner").exists():
        fail("a nested install.xml is part of the parent mod")


def test_lml_package_skips_examples_and_docs(tmp: Path) -> None:
    game = game_dir(tmp / "pack")
    (game / "dinput8.dll").write_bytes(b"loader-already-here")
    staging = tmp / "pack-drop"
    write(staging, "ModLoader/vfs.asi", b"vfs")
    write(staging, "ModLoader/lml.ini", b"ini")
    write(staging, "ModLoader/NLog.dll", b"nlog")
    write(staging, "ModLoader/notes.txt", b"skip")
    write(staging, "ModLoader/version.dll", b"second-loader")
    write(staging, "ModLoader/lml/keep.txt", b"keep")
    write(staging, "Examples/Demo/install.xml", b"<easy/>")
    write(staging, "Mod Manager/ModManager.UI.exe", b"MZ")
    place(staging, game)
    if not (game / "vfs.asi").is_file() or not (game / "lml.ini").is_file():
        fail("Lenny's Mod Loader files should land next to RDR2.exe")
    if (game / "notes.txt").exists():
        fail("the loader readme should not be copied into the game folder")
    if (game / "version.dll").exists():
        fail("a second ASI loader inside the LML zip should be left out")
    if (game / "lml" / "Demo").exists() or (game / "lml" / "Examples").exists():
        fail("bundled LML examples should not be installed")
    if not (game / "ModManager" / "ModManager.UI.exe").is_file():
        fail("Mod Manager should be copied to ModManager")
    if not (game / "lml" / "keep.txt").is_file():
        fail("the lml folder inside ModLoader should be merged")


def test_script_hook_layout(tmp: Path) -> None:
    game = game_dir(tmp / "hook")
    (game / "dinput8.dll").write_bytes(b"newer-loader")
    staging = tmp / "hook-drop"
    write(staging, "bin/ScriptHookRDR2.dll", b"hook")
    write(staging, "bin/dinput8.dll", b"old-loader")
    write(staging, "bin/NativeTrainer.asi", b"trainer")
    write(staging, "readme.txt", b"docs")
    place(staging, game, include_native_trainer=False)
    if (game / "ScriptHookRDR2.dll").read_bytes() != b"hook":
        fail("ScriptHookRDR2.dll should be installed from bin")
    if (game / "dinput8.dll").read_bytes() != b"newer-loader":
        fail("an ASI loader already in the folder should be kept")
    if (game / "NativeTrainer.asi").exists():
        fail("Native Trainer stays out when it is turned off")
    if (game / "readme.txt").exists():
        fail("the script hook readme should not be copied")


def test_dotnet_files(tmp: Path) -> None:
    game = game_dir(tmp / "net")
    staging = tmp / "net-drop"
    write(staging, "ScriptHookRDRDotNet.asi", b"asi")
    write(staging, "ScriptHookRDRDotNet.ini", b"ini")
    write(staging, "ScriptHookRDRNetAPI.dll", b"api")
    write(staging, "ScriptHookRDRNetAPI.xml", b"docs")
    place(staging, game)
    for name in engine.DOTNET_FILES:
        if not (game / name).is_file():
            fail(f"{name} should be installed")
    if (game / "ScriptHookRDRNetAPI.xml").exists():
        fail("the API xml is documentation and should stay out")


def test_archive_and_zip_slip(tmp: Path) -> None:
    game = game_dir(tmp / "zip")
    archive = tmp / "CoolHat.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("CoolHat/install.xml", "<easy/>")
        bundle.writestr("CoolHat/hat.ytd", "tex")
    staging = tmp / "zip-drop"
    staging.mkdir()
    (staging / "CoolHat.zip").write_bytes(archive.read_bytes())
    place(staging, game)
    if not (game / "lml" / "CoolHat" / "install.xml").is_file():
        fail("a zipped LML mod should be unpacked into lml")

    slipped = tmp / "slip.zip"
    with zipfile.ZipFile(slipped, "w") as bundle:
        bundle.writestr("../evil.txt", "nope")
    slip_stage = tmp / "slip-drop"
    slip_stage.mkdir()
    (slip_stage / "slip.zip").write_bytes(slipped.read_bytes())
    place(slip_stage, game)
    if (tmp / "evil.txt").exists() or (game.parent / "evil.txt").exists():
        fail("an archive must not write outside its folder")


def test_refuses_game_folder(tmp: Path) -> None:
    game = game_dir(tmp / "whole")
    staging = tmp / "whole-drop"
    write(staging, "RDR2.exe", b"MZ")
    write(staging, "Extra.dll", b"dll")
    result = place(staging, game)
    if result.ok or (game / "scripts" / "Extra.dll").exists():
        fail("dropping the game folder itself should be refused")


def test_backup_on_replace(tmp: Path) -> None:
    game = game_dir(tmp / "bak")
    (game / "scripts").mkdir()
    (game / "scripts" / "HatScript.dll").write_bytes(b"old")
    staging = tmp / "bak-drop"
    write(staging, "HatScript.dll", b"new")
    place(staging, game)
    if (game / "scripts" / "HatScript.dll").read_bytes() != b"new":
        fail("the new script should replace the old one")
    backups = list((game / "_flashmanager_backup").rglob("HatScript.dll"))
    if not backups or backups[0].read_bytes() != b"old":
        fail("the previous script should be kept in _flashmanager_backup")


def test_story_saves(tmp: Path) -> None:
    if not engine.is_save_name("SRDR30000") or not engine.is_save_name("srdr30015.bak"):
        fail("story save names should be recognized")
    if engine.is_save_name("SRDR3001") or engine.is_save_name("notes.txt"):
        fail("short or unrelated names are not story saves")
    game = game_dir(tmp / "saves")
    profile = tmp / "profiles" / "AB12CD34"
    profile.mkdir(parents=True)
    (profile / "SRDR30015").write_bytes(b"old-autosave")
    staging = tmp / "save-drop"
    write(staging, "pack/SRDR30001", b"slot-bytes")
    write(staging, "pack/SRDR30015", b"new-autosave")
    write(staging, "pack/SRDR30015.bak", b"bak-bytes")
    write(staging, "Solo/HatScript.dll", b"csharp")
    os.environ["FLASHMANAGER_SAVE_BACKUP"] = str(tmp / "backups")
    try:
        result = place(staging, game, save_profile=profile)
    finally:
        os.environ.pop("FLASHMANAGER_SAVE_BACKUP", None)
    if not result.ok:
        fail("a drop with a profile should install the save")
    if (game / "SRDR30001").exists() or (game / "SRDR30015").exists():
        fail("save files must stay out of the game folder")
    if (profile / "SRDR30001").read_bytes() != b"slot-bytes":
        fail("a manual save should land in the profile")
    if (profile / "SRDR30015").read_bytes() != b"new-autosave":
        fail("the autosave slot should be replaced")
    if (profile / "SRDR30015.bak").read_bytes() != b"bak-bytes":
        fail("the matching .bak should be copied with the save")
    if not (game / "scripts" / "HatScript.dll").is_file():
        fail("a script dropped with a save should still go in scripts")
    backed = list((tmp / "backups").rglob("SRDR30015"))
    if not backed or backed[0].read_bytes() != b"old-autosave":
        fail("the previous autosave should be backed up before it is replaced")
    if engine.save_label("SRDR30015") != "SRDR30015 (autosave)":
        fail("SRDR30015 should be described as the autosave")
    if engine.save_label("SRDR30000") != "SRDR30000 (manual slot 1)":
        fail("SRDR30000 should be manual slot 1")

    bare = tmp / "save-only"
    write(bare, "SRDR30004", b"chapter")
    log = engine.Log()
    only = engine.place_saves_only(bare, profile, log)
    if not only.ok or (profile / "SRDR30004").read_bytes() != b"chapter":
        fail("a save can be installed before a game folder is chosen")

    missing = tmp / "save-missing"
    write(missing, "SRDR30002", b"no-profile")
    refused = place(missing, game)
    if refused.ok or (game / "SRDR30002").exists():
        fail("a save with no profile should be refused and kept out of the game")

    packed = tmp / "save-zip"
    packed.mkdir()
    with zipfile.ZipFile(packed / "chapter.zip", "w") as bundle:
        bundle.writestr("John/SRDR30007", b"zipped")
    zipped = place(packed, game, save_profile=profile)
    if not zipped.ok or (profile / "SRDR30007").read_bytes() != b"zipped":
        fail("a zipped story save should be unpacked into the profile")

    mod = tmp / "save-inside-mod"
    write(mod, "Cool Hat/install.xml", b"<easy/>")
    write(mod, "Cool Hat/SRDR30001", b"not-a-slot")
    place(mod, game)
    if not (game / "lml" / "Cool Hat" / "SRDR30001").is_file():
        fail("an SRDR file inside an install.xml mod stays with that mod")
    if (profile / "SRDR30001").read_bytes() == b"not-a-slot":
        fail("an SRDR file inside a mod must not replace the profile save")


def test_popular_overlay_and_picker(tmp: Path) -> None:
    game = game_dir(tmp / "popular")
    original = (game / "RDR2.exe").read_bytes()
    staging = tmp / "popular-drop"
    write(staging, "Cool.asi", b"asi-bytes")
    write(staging, "Cool.json", b"{}")
    write(staging, "README.md", b"notes")
    write(staging, "Helper/tool.exe", b"MZ-tool")
    write(staging, "RDR2.exe", b"bad-game")
    owned = engine.install_overlay(staging, game, engine.Log(), "Cool")
    if "Cool.asi" not in owned or "Helper/tool.exe" not in owned:
        fail(f"overlay should remember the files it copied: {owned}")
    if any(item.lower().endswith(".md") for item in owned):
        fail("a readme should not be recorded as part of the mod")
    if (game / "Cool.asi").read_bytes() != b"asi-bytes" or (game / "Cool.json").read_bytes() != b"{}":
        fail("a popular mod should keep its asi and config")
    if (game / "README.md").exists():
        fail("a readme should not be copied into the game")
    if (game / "Helper" / "tool.exe").read_bytes() != b"MZ-tool":
        fail("a helper program should be copied and not launched")
    if (game / "RDR2.exe").read_bytes() != original:
        fail("a mod must not replace RDR2.exe")
    assets = [
        {"name": "RedLuaV-v0.6.4.zip"},
        {"name": "ChaosModRDR_1.2.1_NoTwitch.zip"},
        {"name": "ChaosModRDR_1.2.1.zip"},
        {"name": "RedLuaRDR2-v0.6.4.zip"},
    ]
    red = engine.pick_popular_asset(assets, "redluardr2")
    chaos = engine.pick_popular_asset(assets, "chaosmodrdr_", "notwitch")
    if not red or red["name"] != "RedLuaRDR2-v0.6.4.zip":
        fail("RedLua should use the RDR2 archive, not the GTA one")
    if not chaos or "notwitch" in chaos["name"].lower():
        fail("Chaos Mod should use the main archive")
    if engine.popular_mod("nope") is not None or engine.popular_mod("vram") is None:
        fail("the popular mod list should only contain known ids")


def test_protected_game_folder(tmp: Path) -> None:
    game = game_dir(tmp / "protect")
    engine._writable_cache.pop(str(game), None)
    if not engine.game_is_writable(game):
        fail("a normal game folder should be writable")
    if not engine.game_is_writable(game):
        fail("the writable check should remember the first answer")
    message = engine.permission_message(game).lower()
    if "administrator" not in message and "security" not in message:
        fail("a blocked write should tell the user how to allow it")
    engine._writable_cache[str(game)] = False
    try:
        engine.ensure_game_writable(game)
        fail("a protected game folder should stop Prep")
    except RuntimeError as exc:
        if "administrator" not in str(exc).lower() and "security" not in str(exc).lower():
            fail(str(exc))
    finally:
        engine._writable_cache.pop(str(game), None)
    target = game / "scripts" / "HatScript.dll"
    target.parent.mkdir()
    target.write_bytes(b"old")
    os.chmod(target, stat.S_IREAD)
    src = tmp / "HatScript.dll"
    src.write_bytes(b"new")
    try:
        try:
            engine.copy_file(src, target, game)
            fail("a read-only game file should be refused with a clear message")
        except RuntimeError as exc:
            text = str(exc).lower()
            if "administrator" not in text and "security" not in text:
                fail(text)
            if "[winerror" in text:
                fail(text)
        if target.read_bytes() != b"old":
            fail("the read-only file should stay as it was")
    finally:
        os.chmod(target, stat.S_IWRITE)


def test_discovery_parsers() -> None:
    paths = engine.library_paths_from_vdf('"path"\t\t"C:\\\\Games\\\\Steam"\n"path" "D:/Steam"')
    if paths[0] != "C:\\Games\\Steam" or paths[1] != "D:/Steam":
        fail(f"libraryfolders.vdf paths parsed wrong: {paths}")
    if engine.acf_installdir('"installdir" "Red Dead Redemption 2"') != "Red Dead Redemption 2":
        fail("appmanifest installdir was not read")
    epic = engine.epic_install_location({
        "DisplayName": "Red Dead Redemption 2",
        "InstallLocation": "D:/Epic/RDR2",
    })
    if epic != "D:/Epic/RDR2":
        fail("Epic manifest was not recognized")
    if engine.epic_install_location({"DisplayName": "Fortnite"}) is not None:
        fail("unrelated Epic games should be ignored")
    if engine.version_key("v10.0.2") <= engine.version_key("v9.4.0"):
        fail("version comparison should put 10 ahead of 9")
    page = '<a href="/files/ScriptHookRDR2_1.0.1491.17.zip">Download</a>'
    page += '<a href="/files/ScriptHookRDR2_SDK_1.0.1207.73.zip">SDK</a>'
    url, version = engine._script_hook_url(page)
    if version != "1.0.1491.17" or not url.endswith("ScriptHookRDR2_1.0.1491.17.zip"):
        fail("Script Hook page should ignore the SDK and keep the runtime zip")
    try:
        engine.safe_rel("../outside.dll")
        fail("paths that leave the drop should be rejected")
    except ValueError:
        pass


def test_file_version() -> None:
    version = engine.file_version(Path(sys.executable))
    if not version or not any(character.isdigit() for character in version):
        fail(f"could not read a file version from Python ({version})")


def test_library_and_remove(tmp: Path) -> None:
    previous = os.environ.get("FLASHMANAGER_CACHE")
    os.environ["FLASHMANAGER_CACHE"] = str(tmp / "empty-cache")
    try:
        _library_and_remove(tmp)
    finally:
        if previous is None:
            os.environ.pop("FLASHMANAGER_CACHE", None)
        else:
            os.environ["FLASHMANAGER_CACHE"] = previous


def _library_and_remove(tmp: Path) -> None:
    game = game_dir(tmp / "library")
    original = (game / "RDR2.exe").read_bytes()
    (game / "ScriptHookRDR2.dll").write_bytes(b"hook")
    (game / "vfs.asi").write_bytes(b"vfs")
    (game / "dinput8.dll").write_bytes(b"loader")
    (game / "NativeTrainer.asi").write_bytes(b"trainer")
    write(game, "lml/Cool Hat/install.xml", b"<easy/>")
    write(game, "lml/Cool Hat/coat.ytd", b"tex")
    write(game, "Extra.asi", b"asi")
    write(game, "Extra.ini", b"ini")
    write(game, "Extra.dll", b"side")
    write(game, "scripts/HatScript.dll", b"csharp")
    write(game, "scripts/HatScript.ini", b"cfg")
    write(game, "Owned.asi", b"owned")
    engine.update_manifest(game, "mod:vram", {
        "version": "v1",
        "name": "Begone VRAM Limit",
        "files": ["Owned.asi"],
    })
    ids = {item["id"] for item in engine.mod_library(game)}
    for expected in ("lml:Cool Hat", "root:Extra.asi", "script:HatScript.dll"):
        if expected not in ids:
            fail(f"library should list {expected}, got {sorted(ids)}")
    for banned in ("root:vfs.asi", "root:NativeTrainer.asi", "root:dinput8.dll", "root:Owned.asi", "root:ScriptHookRDR2.dll"):
        if banned in ids:
            fail(f"library should leave setup and recorded files out ({banned})")

    if engine.game_is_running():
        try:
            engine.remove_mod(game, "lml:Cool Hat", engine.Log())
            fail("remove should stop while Red Dead Redemption 2 is open")
        except RuntimeError as exc:
            if "close red dead" not in str(exc).lower():
                fail(str(exc))
        if not (game / "lml" / "Cool Hat").is_dir():
            fail("a refused remove should leave the mod in place")
    else:
        engine.remove_mod(game, "root:Extra.asi", engine.Log())
        if (game / "Extra.asi").exists():
            fail("remove_mod should delete an asi when the game is closed")
        write(game, "Extra.asi", b"asi")
        write(game, "Extra.ini", b"ini")
        write(game, "Extra.dll", b"side")
    take_out = engine._remove_mod_files
    take_out(game, "lml:Cool Hat", engine.Log())
    if (game / "lml" / "Cool Hat").exists():
        fail("removing an lml mod should delete its folder")
    if not list((game / "_flashmanager_backup").rglob("install.xml")):
        fail("the removed lml mod should be kept in _flashmanager_backup")

    take_out(game, "root:Extra.asi", engine.Log())
    if (game / "Extra.asi").exists() or (game / "Extra.ini").exists() or (game / "Extra.dll").exists():
        fail("removing an asi should also take its ini and dll")
    take_out(game, "script:HatScript.dll", engine.Log())
    if (game / "scripts" / "HatScript.dll").exists() or (game / "scripts" / "HatScript.ini").exists():
        fail("removing a script should also take its ini")
    if not (game / "scripts").is_dir():
        fail("the scripts folder itself should stay")

    for bad in ("lml:../RDR2.exe", "script:../RDR2.exe", "root:ScriptHookRDR2.dll", "root:RDR2.exe"):
        try:
            take_out(game, bad, engine.Log())
            fail(f"{bad} should be refused")
        except RuntimeError:
            pass
        if (game / "RDR2.exe").read_bytes() != original or not (game / "ScriptHookRDR2.dll").is_file():
            fail(f"{bad} must not change the game files")

    (game / "Chaos.asi").write_bytes(b"chaos")
    write(game, "pack/cfg.json", b"{}")
    engine.update_manifest(game, "mod:chaos", {
        "version": "v1",
        "name": "Chaos Mod RDR",
        "files": ["Chaos.asi", "pack/cfg.json", "RDR2.exe"],
    })
    take_out(game, "popular:chaos", engine.Log())
    if (game / "Chaos.asi").exists() or (game / "pack").exists():
        fail("a popular mod should remove its recorded files and empty folders")
    if (game / "RDR2.exe").read_bytes() != original:
        fail("a popular mod remove must leave RDR2.exe alone")
    if "mod:chaos" in (engine.read_manifest(game).get("installed") or {}):
        fail("removing a popular mod should clear its manifest entry")
    if "mod:vram" not in (engine.read_manifest(game).get("installed") or {}):
        fail("removing one mod should leave the others recorded")

    engine.update_manifest(game, "mod:presence", {"version": "v9", "name": "Discord Rich Presence"})
    try:
        take_out(game, "popular:presence", engine.Log())
        fail("a popular mod with no file list and no cached download should stop")
    except RuntimeError as exc:
        if "list of files" not in str(exc).lower():
            fail(str(exc))

    os.environ["FLASHMANAGER_CACHE"] = str(tmp / "cache")
    archive = engine.cache_dir() / "downloads" / "presence-v9-Only.asi.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("Only.asi", b"only")
    (game / "Only.asi").write_bytes(b"only")
    take_out(game, "popular:presence", engine.Log())
    if (game / "Only.asi").exists():
        fail("a cached popular-mod download should be enough to remove its files")
    if "mod:presence" in (engine.read_manifest(game).get("installed") or {}):
        fail("the cached remove should clear the manifest entry")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_loose_rules(tmp)
        test_lml_mod_and_nested_xml(tmp)
        test_lml_package_skips_examples_and_docs(tmp)
        test_script_hook_layout(tmp)
        test_dotnet_files(tmp)
        test_archive_and_zip_slip(tmp)
        test_refuses_game_folder(tmp)
        test_backup_on_replace(tmp)
        test_story_saves(tmp)
        test_popular_overlay_and_picker(tmp)
        test_library_and_remove(tmp)
        test_protected_game_folder(tmp)
    test_discovery_parsers()
    test_file_version()
    print("All FlashManager tests passed.")


if __name__ == "__main__":
    main()
