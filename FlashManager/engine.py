"""Find a Red Dead Redemption 2 install, fetch the story-mode modding tools,
and sort dropped mods into the folders they belong in.

Downloads come only from the authors' own sites: Alexander Blade (dev-c.com),
ThirteenAG (GitHub), Halen84 (GitHub), Lenny's Mod Loader (rdr2mods.com), and
the UnRAR utility from rarlab.com when a .rar cannot be opened another way.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import winreg
import zipfile
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FlashManager/1.0"
SCRIPT_HOOK_PAGE = "https://www.dev-c.com/rdr2/scripthookrdr2/"
UAL_API = "https://api.github.com/repos/ThirteenAG/Ultimate-ASI-Loader/releases/latest"
DOTNET_API = "https://api.github.com/repos/Halen84/ScriptHookRDR2DotNet-V2/releases/latest"
LML_PAGE = "https://www.rdr2mods.com/downloads/rdr2/tools/76-lennys-mod-loader-rdr/"
UNRAR_SFX = "https://www.rarlab.com/rar/unrarw64.exe"
STEAM_APP_ID = "1174180"

ALLOWED_HOSTS = {
    "dev-c.com",
    "www.dev-c.com",
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
    "rarlab.com",
    "www.rarlab.com",
    "rdr2mods.com",
    "www.rdr2mods.com",
}

ROOT_FILES = {
    "scripthookrdr2.dll",
    "scripthookrdrdotnet.asi",
    "scripthookrdrdotnet.ini",
    "scripthookrdrnetapi.dll",
    "vfs.asi",
    "lml.ini",
    "modmanager.core.dll",
    "modmanager.nativeinterop.dll",
    "nlog.dll",
    "easyhook.dll",
    "easyhook64.dll",
    "easyload64.dll",
    "simplehook.dll",
    "nativetrainer.asi",
}
DOTNET_FILES = (
    "ScriptHookRDRDotNet.asi",
    "ScriptHookRDRDotNet.ini",
    "ScriptHookRDRNetAPI.dll",
)
LOADER_NAMES = ("dinput8.dll", "version.dll")
PROTECTED = {
    "rdr2.exe",
    "playrdr2.exe",
    "steam_api64.dll",
    "eossdk-win64-shipping.dll",
    "bink2w64.dll",
    "amd_ags_x64.dll",
    "oo2core_5_win64.dll",
    "nvngx_dlss.dll",
    "dxilconv7.dll",
    "installscript.vdf",
}
ASSET_EXT = {
    ".ytd", ".ydr", ".ydd", ".yft", ".ymap", ".ytyp", ".ymt", ".rel", ".awc",
    ".ynd", ".ynv", ".ybn", ".ycd", ".yed", ".yld", ".ymf", ".ypdb",
}
LOOSE_SKIP_EXT = {
    ".txt", ".md", ".url", ".pdf", ".log", ".pdb", ".xml", ".nfo", ".diz",
    ".lnk", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".html", ".htm",
}
JUNK_NAMES = {"thumbs.db", ".ds_store", "desktop.ini"}
FORBIDDEN_TOP = {
    "_flashmanager_backup",
    "_flashmanager_review",
    "redistributables",
    "x64",
    "12on7",
    "support",
}
EXAMPLE_DIRS = {"examples", "example", "samples", "sample"}
# Story-mode tools Prep installs. Modify can add and remove mods, and leaves these in place.
CORE_LEAVE = set(ROOT_FILES) | {name.lower() for name in LOADER_NAMES} | set(PROTECTED)


class Log:
    """Collects human-readable lines and a 0-100 progress value."""

    def __init__(self):
        self.lines: list[str] = []
        self.percent = 0

    def __call__(self, message: str) -> None:
        self.lines.append(message)

    def progress(self, percent: int) -> None:
        self.percent = max(0, min(100, int(percent)))


class PlaceResult:
    def __init__(self):
        self.lines: list[str] = []
        self.placed: list[dict] = []
        self.ok = True


def is_admin() -> bool:
    """True when this process can write into protected folders such as Program Files."""
    if os.name != "nt":
        return True
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def access_denied(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) == 5


def permission_message(target: Path | None = None) -> str:
    """Plain explanation for WinError 5 on the game folder."""
    if is_admin():
        return (
            "Windows still blocked a change in the game folder. "
            "Right-click the Red Dead Redemption 2 folder, open Properties, then Security, "
            "and allow Modify for your Windows account."
        )
    return (
        "Windows blocked that change. Red Dead Redemption 2 is in a protected folder, "
        "so FlashManager has to run as administrator. "
        "Click Run as administrator and allow the prompt, then try again."
    )


_writable_cache: dict[str, bool] = {}


def game_is_writable(game: Path) -> bool:
    """Probe once per folder. A status poll must not keep writing into the game."""
    key = str(game)
    if key in _writable_cache:
        return _writable_cache[key]
    ok = _probe_game_write(game)
    _writable_cache[key] = ok
    return ok


def _probe_game_write(game: Path) -> bool:
    if not game.is_dir():
        return False
    probe = game / ".flashmanager-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        pass
    return True


def ensure_game_writable(game: Path) -> None:
    if not game_is_writable(game):
        raise RuntimeError(permission_message(game))


def ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if access_denied(exc):
            raise RuntimeError(permission_message(path)) from exc
        raise


def cache_dir() -> Path:
    override = os.environ.get("FLASHMANAGER_CACHE")
    path = Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / "FlashManager"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return cache_dir() / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict) -> None:
    current = load_settings()
    current.update(data)
    settings_path().write_text(json.dumps(current, indent=2), encoding="utf-8")


# --- version and archive helpers -------------------------------------------------

def file_version(path: Path) -> str | None:
    """Read the FileVersion string from a Windows binary."""
    if os.name != "nt" or not path.is_file():
        return None
    version = ctypes_version()
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes_create_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    import ctypes
    from ctypes import wintypes

    lang_ptr = ctypes.c_void_p()
    lang_len = wintypes.UINT()
    text = None
    if version.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(lang_ptr), ctypes.byref(lang_len)):
        if lang_ptr.value and lang_len.value >= 4:
            pair = ctypes.cast(lang_ptr, ctypes.POINTER(ctypes.c_uint16))
            query = f"\\StringFileInfo\\{pair[0]:04x}{pair[1]:04x}\\FileVersion"
            str_ptr = ctypes.c_void_p()
            str_len = wintypes.UINT()
            if version.VerQueryValueW(buf, query, ctypes.byref(str_ptr), ctypes.byref(str_len)) and str_ptr.value:
                text = ctypes.wstring_at(str_ptr.value, str_len.value)
    if text:
        text = text.replace("\x00", "").strip()
        if text:
            return text
    return _fixed_file_version(version, buf)


def ctypes_version():
    import ctypes
    from ctypes import wintypes

    version = ctypes.windll.version
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    version.VerQueryValueW.restype = wintypes.BOOL
    return version


def ctypes_create_buffer(size: int):
    import ctypes

    return ctypes.create_string_buffer(size)


def _fixed_file_version(version, buf) -> str | None:
    import ctypes
    from ctypes import wintypes

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD),
            ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD),
            ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD),
            ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD),
            ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return None
    info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def pe_machine(path: Path) -> int | None:
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return None
    if len(data) < 64 or data[:2] != b"MZ":
        return None
    pe = int.from_bytes(data[0x3C:0x40], "little")
    if pe + 6 > len(data) or data[pe:pe + 4] != b"PE\x00\x00":
        blob = path.read_bytes()
        if pe + 6 > len(blob) or blob[pe:pe + 4] != b"PE\x00\x00":
            return None
        return int.from_bytes(blob[pe + 4:pe + 6], "little")
    return int.from_bytes(data[pe + 4:pe + 6], "little")


def is_pe64(path: Path) -> bool:
    return pe_machine(path) == 0x8664


def version_key(text: str | None) -> tuple:
    if not text:
        return tuple()
    return tuple(int(part) for part in re.findall(r"\d+", text))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", name).strip().rstrip(". ")
    return cleaned or "mod"


def safe_rel(raw: str) -> Path:
    text = urllib.parse.unquote(raw or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ValueError("That file path is not allowed.")
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part.endswith(" ") or part.endswith(".") or ":" in part:
            raise ValueError("That file path is not allowed.")
        parts.append(part)
    if not parts:
        raise ValueError("That file path is not allowed.")
    return Path(*parts)


def _host_allowed(host: str | None) -> bool:
    if not host:
        return False
    if host in ALLOWED_HOSTS:
        return True
    return host.endswith(".githubusercontent.com")


class _GuardRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlparse(newurl).hostname
        if not _host_allowed(host):
            raise RuntimeError(f"Refusing a redirect to {host}.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_GuardRedirect)


def fetch_bytes(url: str, referer: str | None = None, timeout: int = 90) -> tuple[bytes, str]:
    host = urllib.parse.urlparse(url).hostname
    if not _host_allowed(host):
        raise RuntimeError(f"Refusing to download from {host}.")
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with _opener().open(request, timeout=timeout) as response:
            final = response.geturl()
            final_host = urllib.parse.urlparse(final).hostname
            if not _host_allowed(final_host):
                raise RuntimeError(f"Refusing a download from {final_host}.")
            return response.read(), final
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{url} returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the download. {exc.reason}") from exc


def fetch_text(url: str, referer: str | None = None) -> str:
    data, _final = fetch_bytes(url, referer=referer)
    return data.decode("utf-8", errors="replace")


def download_file(url: str, dest: Path, referer: str | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data, _final = fetch_bytes(url, referer=referer, timeout=180)
    if data[:1] == b"<" or data[:15].lower().startswith(b"<!doctype"):
        raise RuntimeError("The site returned a web page instead of the file.")
    dest.write_bytes(data)
    return dest


def looks_like_zip(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"PK"
    except OSError:
        return False


def safe_unzip(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in name.split("/"):
                raise RuntimeError(f"The archive contains an unsafe path ({name}).")
        bundle.extractall(dest)
    _assert_tree_inside(dest)


def _assert_tree_inside(root: Path) -> None:
    base = root.resolve()
    for path in root.rglob("*"):
        path.resolve().relative_to(base)


def _run_hidden(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )


def _try_tar(archive: Path, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    result = _run_hidden(["tar", "-xf", str(archive), "-C", str(dest)])
    if result.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        return False
    try:
        _assert_tree_inside(dest)
    except ValueError:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("The archive tried to write files outside its folder.") from None
    return True


def ensure_unrar(log: Log) -> Path:
    folder = cache_dir() / "unrar"
    exe = folder / "UnRAR.exe"
    if exe.exists():
        return exe
    folder.mkdir(parents=True, exist_ok=True)
    sfx = folder / "unrarw64.exe"
    log("Downloading the UnRAR utility so .rar mods can be opened.")
    download_file(UNRAR_SFX, sfx)
    result = _run_hidden([str(sfx), "-s1"], cwd=folder)
    if result.returncode != 0 or not exe.exists():
        raise RuntimeError("Could not unpack UnRAR. Extract the .rar yourself and drop the folder.")
    return exe


def find_7z() -> Path | None:
    found = shutil.which("7z")
    if found:
        return Path(found)
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
    ):
        if candidate.exists():
            return candidate
    return None


def extract_archive(archive: Path, dest: Path, log: Log) -> None:
    ext = archive.suffix.lower()
    if ext in {".zip", ".oiv"}:
        if not looks_like_zip(archive):
            raise RuntimeError("That file is not a zip archive.")
        safe_unzip(archive, dest)
        return
    if _try_tar(archive, dest):
        return
    if ext == ".rar":
        unrar = ensure_unrar(log)
        dest.mkdir(parents=True, exist_ok=True)
        result = _run_hidden([str(unrar), "x", "-y", "-o+", str(archive), str(dest) + "\\"], cwd=dest)
        if result.returncode != 0:
            raise RuntimeError("UnRAR could not open that archive.")
        _assert_tree_inside(dest)
        return
    if ext == ".7z":
        seven = find_7z()
        if seven is None:
            raise RuntimeError("This is a .7z archive. Install 7-Zip, or extract it and drop the folder.")
        dest.mkdir(parents=True, exist_ok=True)
        result = _run_hidden([str(seven), "x", "-y", f"-o{dest}", str(archive)])
        if result.returncode != 0:
            raise RuntimeError("7-Zip could not open that archive.")
        _assert_tree_inside(dest)
        return
    raise RuntimeError(f"Cannot open {archive.suffix} archives.")


# --- game discovery --------------------------------------------------------------

def library_paths_from_vdf(text: str) -> list[str]:
    return [match.replace("\\\\", "\\") for match in re.findall(r'"path"\s+"([^"]*)"', text)]


def acf_installdir(text: str) -> str | None:
    match = re.search(r'"installdir"\s+"([^"]+)"', text)
    return match.group(1) if match else None


def epic_install_location(data: dict) -> str | None:
    label = f"{data.get('DisplayName') or ''} {data.get('AppName') or ''}"
    compact = re.sub(r"[^a-z0-9]", "", label.lower())
    if "reddeadredemption2" not in compact:
        return None
    location = data.get("InstallLocation")
    return str(location) if location else None


def _registry_string(root, subkey: str, name: str) -> str | None:
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value else None


def _steam_roots() -> list[Path]:
    steam = _registry_string(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
    roots: list[Path] = []
    if steam:
        roots.append(Path(steam))
        vdf = Path(steam) / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                for folder in library_paths_from_vdf(vdf.read_text(encoding="utf-8", errors="replace")):
                    roots.append(Path(folder))
            except OSError:
                pass
    return roots


def _steam_installs() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for root in _steam_roots():
        manifest = root / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf"
        names = ["Red Dead Redemption 2"]
        if manifest.is_file():
            try:
                installdir = acf_installdir(manifest.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                installdir = None
            if installdir:
                names.insert(0, installdir)
        for name in names:
            folder = root / "steamapps" / "common" / name
            if (folder / "RDR2.exe").is_file():
                key = str(folder.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(folder)
                break
    return found


def _epic_installs() -> list[Path]:
    folder = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    found: list[Path] = []
    if not folder.is_dir():
        return found
    for item in folder.glob("*.item"):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        location = epic_install_location(data)
        if not location:
            continue
        path = Path(location)
        game = normalize_game_dir(path)
        if game is not None:
            found.append(game)
    return found


def _uninstall_installs() -> list[Path]:
    found: list[Path] = []
    bases = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in bases:
            try:
                with winreg.OpenKey(hive, base) as key:
                    count = winreg.QueryInfoKey(key)[0]
                    names = [winreg.EnumKey(key, index) for index in range(count)]
            except OSError:
                continue
            for name in names:
                try:
                    with winreg.OpenKey(hive, base + "\\" + name) as sub:
                        display, _kind = winreg.QueryValueEx(sub, "DisplayName")
                        if "red dead redemption 2" not in str(display).lower():
                            continue
                        location, _kind = winreg.QueryValueEx(sub, "InstallLocation")
                except OSError:
                    continue
                game = normalize_game_dir(Path(str(location)))
                if game is not None:
                    found.append(game)
    return found


def _nearby_install(start: Path) -> Path | None:
    current = start.resolve()
    for _depth in range(4):
        game = normalize_game_dir(current)
        if game is not None:
            return game
        if current.parent == current:
            break
        current = current.parent
    parent = start.resolve().parent
    if parent.is_dir():
        try:
            children = list(parent.iterdir())
        except OSError:
            children = []
        for child in children:
            game = normalize_game_dir(child)
            if game is not None:
                return game
    return None


def normalize_game_dir(path: Path) -> Path | None:
    try:
        path = path.expanduser()
    except OSError:
        return None
    if (path / "RDR2.exe").is_file():
        return path
    if not path.is_dir():
        return None
    try:
        children = list(path.iterdir())
    except OSError:
        return None
    for child in children:
        if child.is_dir() and (child / "RDR2.exe").is_file():
            return child
    return None


def platform_of(folder: Path) -> str:
    try:
        names = {item.name.lower() for item in folder.iterdir() if item.is_file()}
    except OSError:
        names = set()
    text = str(folder).lower()
    if "steam_api64.dll" in names or "steamapps" in text:
        return "Steam"
    if "eossdk-win64-shipping.dll" in names or "epic games" in text:
        return "Epic"
    if "rockstar games" in text:
        return "Rockstar"
    return "Unknown"


def discover_games(tool_dir: Path | None = None) -> list[dict]:
    folders: list[Path] = []
    for finder in (_steam_installs, _epic_installs, _uninstall_installs):
        try:
            folders.extend(finder())
        except OSError:
            continue
    if tool_dir is not None:
        nearby = _nearby_install(tool_dir)
        if nearby is not None:
            folders.insert(0, nearby)
    unique: list[dict] = []
    seen: set[str] = set()
    for folder in folders:
        try:
            key = str(folder.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append({
            "path": str(folder),
            "platform": platform_of(folder),
            "version": file_version(folder / "RDR2.exe"),
        })
    return unique


def process_running(image: str) -> bool:
    result = _run_hidden(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"])
    return image.lower() in (result.stdout or "").lower() and "no tasks" not in (result.stdout or "").lower()


def game_is_running() -> bool:
    return process_running("RDR2.exe") or process_running("PlayRDR2.exe")


def _known_folder(data1: int, data2: int, data3: int, data4: bytes, fallback: Path) -> Path:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder_id = GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))
    shell = ctypes.windll.shell32
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.HRESULT
    pointer = ctypes.c_wchar_p()
    if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)) != 0 or not pointer.value:
        return fallback
    try:
        return Path(pointer.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(pointer)


def downloads_dir() -> Path:
    return _known_folder(
        0x374DE290, 0x123F, 0x4565,
        bytes((0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B)),
        Path.home() / "Downloads",
    )


def documents_dir() -> Path:
    return _known_folder(
        0xFDD39AD0, 0x238F, 0x46AF,
        bytes((0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)),
        Path.home() / "Documents",
    )


SAVE_NAME = re.compile(r"^srdr3(\d{4})(\.bak)?$", re.IGNORECASE)


def is_save_name(name: str) -> bool:
    """Story saves are SRDR30000 (manual slot 1) through SRDR30014, plus SRDR30015 (autosave)."""
    return SAVE_NAME.fullmatch(name) is not None


def save_label(name: str) -> str:
    match = SAVE_NAME.fullmatch(name)
    if match is None:
        return name
    number = int(match.group(1))
    slot = "autosave" if number == 15 else f"manual slot {number + 1}"
    if match.group(2):
        slot += ", backup"
    return f"{name} ({slot})"


def profiles_root() -> Path:
    return documents_dir() / "Rockstar Games" / "Red Dead Redemption 2" / "Profiles"


def _profile_slots(folder: Path) -> list[str]:
    try:
        names = [
            path.name for path in folder.iterdir()
            if path.is_file() and is_save_name(path.name) and not path.name.lower().endswith(".bak")
        ]
    except OSError:
        return []
    names.sort(key=str.lower)
    return names


def discover_profiles() -> list[dict]:
    root = profiles_root()
    if not root.is_dir():
        return []
    found = []
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir() or re.fullmatch(r"[0-9A-Fa-f]{6,32}", child.name) is None:
            continue
        found.append({"id": child.name, "path": str(child), "slots": _profile_slots(child)})
    found.sort(key=lambda item: item["id"].lower())
    return found


def normalize_profile_dir(path: Path) -> Path | None:
    try:
        path = path.expanduser()
    except OSError:
        return None
    if not path.is_dir():
        return None
    if re.fullmatch(r"[0-9A-Fa-f]{6,32}", path.name):
        return path
    try:
        children = [
            child for child in path.iterdir()
            if child.is_dir() and re.fullmatch(r"[0-9A-Fa-f]{6,32}", child.name)
        ]
    except OSError:
        return None
    if len(children) == 1:
        return children[0]
    return None


def save_backup_base() -> Path:
    override = os.environ.get("FLASHMANAGER_SAVE_BACKUP")
    if override:
        return Path(override)
    return documents_dir() / "Rockstar Games" / "Red Dead Redemption 2" / "_flashmanager_save_backup"


def install_saves(files: list[Path], profile: Path, log: Log) -> list[dict]:
    """Copy SRDR save files into a Rockstar profile. The previous file is kept aside."""
    if not profile.is_dir():
        raise RuntimeError("That Rockstar profile folder is missing. Launch story mode once, then try again.")
    placed: list[dict] = []
    backup_root: Path | None = None
    backed: set[str] = set()
    for src in files:
        if not src.is_file() or not is_save_name(src.name):
            continue
        dest = profile / src.name
        try:
            same = src.resolve() == dest.resolve()
        except OSError:
            same = False
        if same:
            log(f"{save_label(src.name)} is already in the profile.")
            _record(placed, save_label(src.name), f"Profiles/{profile.name}/{src.name}")
            continue
        key = dest.name.lower()
        if dest.is_file() and key not in backed:
            if backup_root is None:
                stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_root = save_backup_base() / profile.name / stamp
                backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup_root / dest.name)
            backed.add(key)
        shutil.copy2(src, dest)
        log(f"Installed {save_label(src.name)}.")
        _record(placed, save_label(src.name), f"Profiles/{profile.name}/{src.name}")
    if backup_root is not None:
        log(f"The previous save files are in {backup_root}.")
    return placed


def _save_files_in(staging: Path, consumed: set[Path]) -> list[Path]:
    found = []
    for path in staging.rglob("*"):
        if not path.is_file() or not is_save_name(path.name) or _consumed(path, consumed):
            continue
        if any((parent / "install.xml").is_file() for parent in path.parents if parent != path):
            continue
        found.append(path)
    found.sort(key=lambda path: str(path).lower())
    return found


def dotnet48_installed() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "Release")
            return int(value) >= 528040
    except (OSError, ValueError, TypeError):
        return False


def vcpp_x64_installed() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "Installed")
            return int(value) == 1
    except (OSError, ValueError, TypeError):
        return False


# --- manifest and install --------------------------------------------------------

def manifest_path(game: Path) -> Path:
    return game / "_flashmanager_backup" / "manifest.json"


def read_manifest(game: Path) -> dict:
    path = manifest_path(game)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_manifest(game: Path, data: dict) -> None:
    path = manifest_path(game)
    ensure_dir(path.parent)
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        if access_denied(exc):
            raise RuntimeError(permission_message(path)) from exc
        raise
    note = path.parent / "README.txt"
    if not note.exists():
        note.write_text(
            "FlashManager keeps the previous copy of any file it replaces in this folder.\n"
            "You can delete a backup once the game is running the way you want.\n",
            encoding="utf-8",
        )


def update_manifest(game: Path, key: str, info: dict) -> None:
    data = read_manifest(game)
    installed = data.setdefault("installed", {})
    installed[key] = info
    write_manifest(game, data)


def _component(present: bool, version: str | None, detail: str) -> dict:
    return {"present": present, "version": version, "detail": detail}


def scan_game(game: Path | None) -> dict:
    if game is None or not (game / "RDR2.exe").is_file():
        empty = _component(False, None, "Choose the game folder")
        return {
            "game": None,
            "components": {
                "script_hook": empty,
                "asi_loader": empty,
                "lml": empty,
                "dotnet": empty,
                "trainer": _component(False, None, "Optional"),
            },
        }
    manifest = read_manifest(game).get("installed", {})
    hook = game / "ScriptHookRDR2.dll"
    hook_version = (manifest.get("script_hook") or {}).get("version") or file_version(hook)
    loader_file = None
    for name in LOADER_NAMES:
        if (game / name).is_file():
            loader_file = name
            break
    loader_meta = manifest.get("asi_loader") or {}
    loader_version = loader_meta.get("version") if loader_meta.get("file") == loader_file else loader_meta.get("version")
    if loader_file and not loader_version:
        loader_version = file_version(game / loader_file)
    lml_ready = (game / "vfs.asi").is_file() and (game / "lml.ini").is_file()
    lml_version = (manifest.get("lml") or {}).get("version")
    dotnet_ready = all((game / name).is_file() for name in DOTNET_FILES)
    dotnet_version = (manifest.get("dotnet") or {}).get("version")
    return {
        "game": {
            "path": str(game),
            "platform": platform_of(game),
            "version": file_version(game / "RDR2.exe"),
            "running": game_is_running(),
        },
        "components": {
            "script_hook": _component(hook.is_file(), hook_version, "ScriptHookRDR2.dll"),
            "asi_loader": _component(
                loader_file is not None,
                loader_version,
                loader_file or "dinput8.dll or version.dll",
            ),
            "lml": _component(lml_ready, lml_version, "vfs.asi and lml.ini"),
            "dotnet": _component(dotnet_ready, dotnet_version, "ScriptHookRDRDotNet.asi"),
            "trainer": _component((game / "NativeTrainer.asi").is_file(), None, "NativeTrainer.asi"),
        },
    }


def _backup(game: Path, dest: Path) -> None:
    rel = dest.resolve().relative_to(game.resolve())
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = game / "_flashmanager_backup" / stamp / rel
    ensure_dir(target.parent)
    try:
        if dest.is_dir() and not dest.is_symlink():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(dest, target)
        else:
            shutil.copy2(dest, target)
    except OSError as exc:
        if access_denied(exc):
            raise RuntimeError(permission_message(target)) from exc
        raise


def _forbidden(rel: Path) -> bool:
    if not rel.parts:
        return True
    return rel.parts[0].lower() in FORBIDDEN_TOP or rel.name.lower() in PROTECTED


def copy_file(src: Path, dest: Path, game: Path) -> str:
    """Copy one file into the game tree. Returns new, replaced, same, or blocked."""
    game_root = game.resolve()
    try:
        rel = dest.resolve().relative_to(game_root)
    except ValueError:
        return "blocked"
    if _forbidden(rel):
        return "blocked"
    try:
        ensure_dir(dest.parent)
        dest_resolved = dest.resolve()
        if dest_resolved.is_file() and dest_resolved.stat().st_size == src.stat().st_size:
            if sha256(dest_resolved) == sha256(src):
                return "same"
        if dest_resolved.exists():
            _backup(game, dest_resolved)
            outcome = "replaced"
        else:
            outcome = "new"
        shutil.copy2(src, dest_resolved)
    except OSError as exc:
        if access_denied(exc):
            raise RuntimeError(permission_message(dest)) from exc
        raise
    return outcome


def _warn_machine(path: Path, log: Log) -> None:
    machine = pe_machine(path)
    if machine == 0x14C:
        log(f"{path.name} looks 32-bit. RDR2 is 64-bit and will not load it.")


def _record(placed: list[dict], label: str, where: str, tone: str = "ok") -> None:
    placed.append({"label": label, "where": where, "tone": tone})


def _review_note(game: Path) -> None:
    folder = game / "_flashmanager_review"
    ensure_dir(folder)
    note = folder / "README.txt"
    if note.exists():
        return
    note.write_text(
        "FlashManager put files here when they should not go straight into the game.\n\n"
        "needs-install-xml  Game assets with no install.xml. Lenny's Mod Loader will not\n"
        "                    load them until the mod says which game archive they replace.\n"
        "openiv             OpenIV packages. Drop an LML copy of the mod, or install these\n"
        "                    with OpenIV.\n"
        "programs           Executables are not launched and are not placed next to RDR2.exe.\n"
        "loaders            Extra ASI-loader DLLs, kept out so plugins are not loaded twice.\n",
        encoding="utf-8",
    )


def _park(src: Path, dest_dir: Path, game: Path, log: Log, placed: list[dict], why: str) -> None:
    _review_note(game)
    ensure_dir(dest_dir)
    target = dest_dir / src.name
    if src.resolve() == target.resolve():
        return
    if src.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)
    rel = target.resolve().relative_to(game.resolve()).as_posix()
    log(f"{why} Kept at {rel}.")
    _record(placed, src.name, rel, "warn")


def _copy_tree(
    src_dir: Path,
    dest_dir: Path,
    game: Path,
    log: Log,
    label: str,
    placed: list[dict],
    *,
    skip_root_docs: bool = False,
    skip_loaders: bool = False,
) -> None:
    counts = {"new": 0, "replaced": 0, "same": 0, "blocked": 0}
    files = [path for path in src_dir.rglob("*") if path.is_file() and path.name.lower() not in JUNK_NAMES]
    for src in files:
        rel = src.relative_to(src_dir)
        if skip_root_docs and len(rel.parts) == 1 and src.suffix.lower() in LOOSE_SKIP_EXT:
            continue
        if skip_loaders and src.name.lower() in LOADER_NAMES and _loader_present(game):
            log(f"Left out {src.name} so it does not fight the ASI loader already in the game folder.")
            continue
        outcome = copy_file(src, dest_dir / rel, game)
        counts[outcome] = counts.get(outcome, 0) + 1
    try:
        where = dest_dir.resolve().relative_to(game.resolve()).as_posix()
    except ValueError:
        where = label
    if where == ".":
        where = "game folder"
    if counts["blocked"]:
        log(f"Some files in {label} were refused because they would replace the game itself.")
    if counts["new"] or counts["replaced"]:
        log(f"Placed {label} ({counts['new']} new, {counts['replaced']} replaced).")
        _record(placed, label, where, "ok")
    elif counts["same"]:
        log(f"Already in place: {label}.")
        _record(placed, label, where, "ok")
    elif files:
        log(f"Nothing from {label} was copied.")


def _consume(path: Path, consumed: set[Path]) -> None:
    resolved = path.resolve()
    consumed.add(resolved)
    if path.is_dir():
        for child in path.rglob("*"):
            consumed.add(child.resolve())


def _consumed(path: Path, consumed: set[Path]) -> bool:
    current = path.resolve()
    while True:
        if current in consumed:
            return True
        if current.parent == current:
            return False
        current = current.parent


def _loader_present(game: Path) -> str | None:
    for name in LOADER_NAMES:
        if (game / name).is_file():
            return name
    return None


def _extract_loose_archives(staging: Path, log: Log, consumed: set[Path]) -> None:
    for _round in range(2):
        archives = []
        for path in staging.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".zip", ".rar", ".7z", ".oiv"}:
                continue
            if _consumed(path, consumed):
                continue
            if len(path.relative_to(staging).parts) > 3:
                continue
            if any((parent / "install.xml").is_file() for parent in path.parents if parent != path):
                continue
            archives.append(path)
        if not archives:
            return
        for archive in archives:
            dest = staging / "_extracted" / sanitize_name(archive.stem)
            counter = 2
            while dest.exists():
                dest = staging / "_extracted" / f"{sanitize_name(archive.stem)}-{counter}"
                counter += 1
            try:
                extract_archive(archive, dest, log)
            except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
                log(f"Could not open {archive.name}. {exc}")
                _consume(archive, consumed)
                continue
            _consume(archive, consumed)
            if archive.suffix.lower() == ".oiv" or (dest / "assembly.xml").is_file() or any(dest.glob("*/assembly.xml")):
                # Marked here and parked later by the caller via a sentinel file name.
                marker = dest / ".flashmanager-openiv"
                try:
                    marker.write_text("openiv", encoding="utf-8")
                except OSError:
                    pass


def _mod_name(mod_root: Path) -> str:
    name = mod_root.name
    if name.lower() in {"_extracted", "modloader", "bin", "files", "mod", "lml"}:
        name = mod_root.parent.name
    return sanitize_name(name)


def _install_staged_saves(staging: Path, consumed: set[Path], profile: Path | None, log: Log, placed: list[dict]) -> bool:
    """Copy story saves out of a drop. Returns True when saves were found but not installed."""
    files = _save_files_in(staging, consumed)
    for path in files:
        _consume(path, consumed)
    if not files:
        return False
    if profile is not None and Path(profile).is_dir():
        placed.extend(install_saves(files, Path(profile), log))
        return False
    log(
        "Found story save files, but no Rockstar profile is selected. "
        "Launch story mode once so the profile folder exists, pick it on this page, then drop the save again."
    )
    return True


def place_saves_only(staging: Path, profile: Path | None, log: Log) -> PlaceResult:
    """Install a drop that is only story saves, before a game folder has been chosen."""
    result = PlaceResult()
    consumed: set[Path] = set()
    _extract_loose_archives(staging, log, consumed)
    if any(path.name.lower() == "rdr2.exe" for path in staging.rglob("*") if path.is_file()):
        log("That drop contains RDR2.exe. Drop the save, not the game folder.")
        result.ok = False
        result.lines = log.lines
        return result
    missing = _install_staged_saves(staging, consumed, profile, log, result.placed)
    if not result.placed:
        if not missing:
            log("Choose the game folder before dropping mods. A story save is a file named SRDR30000 through SRDR30015.")
        result.ok = False
    result.lines = log.lines
    return result


def place_tree(
    staging: Path,
    game: Path,
    log: Log,
    *,
    include_native_trainer: bool = True,
    loader_name: str = "dinput8.dll",
    save_profile: Path | None = None,
) -> PlaceResult:
    """Sort a dropped folder into the game directory."""
    result = PlaceResult()
    placed = result.placed
    if loader_name not in LOADER_NAMES:
        loader_name = "dinput8.dll"
    if not (game / "RDR2.exe").is_file():
        log("That folder does not contain RDR2.exe.")
        result.ok = False
        result.lines = log.lines
        return result
    if any(path.name.lower() == "rdr2.exe" for path in staging.rglob("*") if path.is_file()):
        log("That drop contains RDR2.exe. Drop the mod, not the game folder.")
        result.ok = False
        result.lines = log.lines
        return result

    consumed: set[Path] = set()
    _extract_loose_archives(staging, log, consumed)
    save_missing = _install_staged_saves(staging, consumed, save_profile, log, placed)

    # OpenIV packages extracted above.
    for marker in staging.rglob(".flashmanager-openiv"):
        package = marker.parent
        if _consumed(package, consumed):
            continue
        marker.unlink(missing_ok=True)
        _park(
            package,
            game / "_flashmanager_review" / "openiv",
            game,
            log,
            placed,
            f"{package.name} is an OpenIV package. Lenny's Mod Loader does not load it.",
        )
        _consume(package, consumed)

    modloaders = [
        path.parent
        for path in staging.rglob("vfs.asi")
        if path.parent.name.lower() == "modloader" and not _consumed(path, consumed)
    ]
    for modloader in modloaders:
        _copy_tree(
            modloader,
            game,
            game,
            log,
            "Lenny's Mod Loader",
            placed,
            skip_root_docs=True,
            skip_loaders=True,
        )
        _consume(modloader, consumed)
        update_manifest(game, "lml", {"version": _lml_version_guess(staging) or "installed", "files": ["vfs.asi", "lml.ini"]})

    if modloaders:
        for folder in staging.rglob("*"):
            if folder.is_dir() and folder.name.lower() in EXAMPLE_DIRS and not _consumed(folder, consumed):
                log(f"Left out {folder.name}. Drop one of those folders later if you want it installed.")
                _consume(folder, consumed)

    for exe in list(staging.rglob("ModManager.UI.exe")):
        if _consumed(exe, consumed):
            continue
        _copy_tree(exe.parent, game / "ModManager", game, log, "Mod Manager", placed)
        _consume(exe.parent, consumed)

    for dll in list(staging.rglob("ScriptHookRDR2.dll")):
        if _consumed(dll, consumed):
            continue
        _warn_machine(dll, log)
        outcome = copy_file(dll, game / "ScriptHookRDR2.dll", game)
        if outcome == "blocked":
            log("Refused to replace a protected file while copying Script Hook.")
        elif outcome == "same":
            log("Already in place: ScriptHookRDR2.dll.")
        else:
            log("Placed ScriptHookRDR2.dll.")
            _record(placed, "Script Hook RDR2", "ScriptHookRDR2.dll")
        parent = dll.parent
        if parent.name.lower() == "bin":
            trainer = parent / "NativeTrainer.asi"
            bundled = parent / "dinput8.dll"
            if trainer.is_file():
                if include_native_trainer:
                    copy_file(trainer, game / "NativeTrainer.asi", game)
                    log("Placed NativeTrainer.asi. Press F4 in story mode.")
                    _record(placed, "Native Trainer", "NativeTrainer.asi")
                else:
                    log("Left out NativeTrainer.asi. Turn it on in Prep if you want the F4 trainer.")
            if bundled.is_file():
                if _loader_present(game):
                    log("Kept the ASI loader already in the game folder and left out Script Hook's older dinput8.dll.")
                else:
                    copy_file(bundled, game / "dinput8.dll", game)
                    log("Placed Script Hook's dinput8.dll. Prep can replace it with Ultimate ASI Loader.")
                    _record(placed, "ASI loader", "dinput8.dll")
            _consume(parent, consumed)
        _consume(dll, consumed)

    for name in DOTNET_FILES:
        for source in list(staging.rglob(name)):
            if _consumed(source, consumed):
                continue
            outcome = copy_file(source, game / name, game)
            if outcome != "blocked":
                if outcome != "same":
                    log(f"Placed {name}.")
                else:
                    log(f"Already in place: {name}.")
                _record(placed, "ScriptHookRDR2 .NET", name)
            _consume(source, consumed)
    if (game / "ScriptHookRDRDotNet.asi").is_file():
        ensure_dir(game / "scripts")

    for xml in list(staging.rglob("install.xml")):
        if _consumed(xml, consumed):
            continue
        if any((ancestor / "install.xml").is_file() for ancestor in xml.parents if ancestor != xml.parent):
            continue
        mod_root = xml.parent
        # A scripts directory is handled below unless it is actually an LML mod.
        name = _mod_name(mod_root)
        dest = game / "lml" / name
        _copy_tree(mod_root, dest, game, log, name, placed)
        for asi in mod_root.glob("*.asi"):
            copy_file(asi, game / asi.name, game)
            ini = mod_root / (asi.stem + ".ini")
            if ini.is_file():
                copy_file(ini, game / ini.name, game)
            log(f"Also placed {asi.name} next to RDR2.exe so the ASI loader can see it.")
        _consume(mod_root, consumed)

    for scripts_dir in list(staging.rglob("*")):
        if not scripts_dir.is_dir() or scripts_dir.name.lower() != "scripts":
            continue
        if _consumed(scripts_dir, consumed) or (scripts_dir / "install.xml").is_file():
            continue
        _copy_tree(scripts_dir, game / "scripts", game, log, "scripts", placed)
        _consume(scripts_dir, consumed)

    for lml_dir in list(staging.rglob("*")):
        if not lml_dir.is_dir() or lml_dir.name.lower() != "lml" or _consumed(lml_dir, consumed):
            continue
        for child in lml_dir.iterdir():
            if child.name.lower() in JUNK_NAMES:
                continue
            target = game / "lml" / child.name
            if child.is_dir():
                _copy_tree(child, target, game, log, child.name, placed)
            elif child.is_file():
                copy_file(child, target, game)
                log(f"Placed lml/{child.name}.")
        _consume(lml_dir, consumed)

    for path in list(staging.rglob("*")):
        if not path.is_file() or _consumed(path, consumed):
            continue
        _place_loose(path, staging, game, log, placed, loader_name)
        _consume(path, consumed)

    if (game / "vfs.asi").is_file() and not (game / "lml.ini").is_file():
        log("vfs.asi is in the game folder, but lml.ini is missing. Drop the full Lenny's Mod Loader download.")
    if (game / "ScriptHookRDR2.dll").is_file() and _loader_present(game) is None:
        log("Script Hook is in place, but there is no ASI loader yet. Run Prep.")
    owned = [item for item in (_loader_present(game),) if item]
    present = [name for name in LOADER_NAMES if (game / name).is_file()]
    if len(present) > 1:
        log(
            f"Both {' and '.join(present)} are in the game folder. "
            "Two ASI loaders will load every plugin twice and the game can crash. "
            "Keep one of them."
        )
    if save_missing and not placed:
        result.ok = False
    result.lines = log.lines
    return result


def _place_loose(path: Path, staging: Path, game: Path, log: Log, placed: list[dict], loader_name: str) -> None:
    name = path.name.lower()
    ext = path.suffix.lower()
    if name in JUNK_NAMES or ext in LOOSE_SKIP_EXT:
        return
    if name in PROTECTED:
        log(f"Refused {path.name}. That file belongs to the game.")
        return
    if ext == ".rpf":
        _park(path, game / "_flashmanager_review" / "rpf", game, log, placed, f"{path.name} is a game archive.")
        return
    if ext == ".exe":
        _park(
            path,
            game / "_flashmanager_review" / "programs",
            game,
            log,
            placed,
            f"Did not run {path.name}.",
        )
        return
    if name in LOADER_NAMES:
        existing = _loader_present(game)
        if existing and existing != name:
            _park(
                path,
                game / "_flashmanager_review" / "loaders",
                game,
                log,
                placed,
                f"Left {path.name} out so it does not fight {existing}.",
            )
            return
        _warn_machine(path, log)
        outcome = copy_file(path, game / path.name, game)
        if outcome != "blocked":
            log(f"Placed {path.name}.")
            _record(placed, "ASI loader", path.name)
        return
    if name in ROOT_FILES or ext == ".asi":
        _warn_machine(path, log)
        outcome = copy_file(path, game / path.name, game)
        _copy_sibling_ini(path, game, game)
        if outcome != "blocked":
            log(f"Placed {path.name} next to RDR2.exe.")
            _record(placed, path.name, path.name)
        return
    if ext == ".dll":
        _warn_machine(path, log)
        siblings = [item.suffix.lower() for item in path.parent.iterdir() if item.is_file()]
        if ".asi" in siblings:
            outcome = copy_file(path, game / path.name, game)
            where = path.name
            log(f"Placed {path.name} next to RDR2.exe, beside its ASI plugin.")
        else:
            outcome = copy_file(path, game / "scripts" / path.name, game)
            where = f"scripts/{path.name}"
            log(f"Placed {path.name} in scripts.")
        _copy_sibling_ini(path, game / "scripts" if where.startswith("scripts/") else game, game)
        if outcome != "blocked":
            _record(placed, path.name, where)
        return
    if ext == ".cs":
        copy_file(path, game / "scripts" / path.name, game)
        log(f"Placed {path.name} in scripts.")
        _record(placed, path.name, f"scripts/{path.name}")
        return
    if ext == ".ini":
        dest_dir = _ini_dir(path, game)
        copy_file(path, dest_dir / path.name, game)
        rel = (dest_dir / path.name).resolve().relative_to(game.resolve()).as_posix()
        log(f"Placed {rel}.")
        _record(placed, path.name, rel)
        return
    if ext in ASSET_EXT:
        group = sanitize_name(path.parent.name if path.parent != staging else path.stem)
        _park(
            path,
            game / "_flashmanager_review" / "needs-install-xml" / group,
            game,
            log,
            placed,
            f"{path.name} has no install.xml, so Lenny's Mod Loader would ignore it in lml.",
        )
        return
    log(f"Left out {path.name}.")


def _copy_sibling_ini(source: Path, dest_dir: Path, game: Path) -> None:
    ini = source.with_suffix(".ini")
    if ini.is_file():
        copy_file(ini, dest_dir / ini.name, game)


def _ini_dir(path: Path, game: Path) -> Path:
    stem = path.stem
    if (game / f"{stem}.asi").is_file() or (game / f"{stem}.dll").is_file():
        return game
    if (game / "scripts" / f"{stem}.dll").is_file() or (game / "scripts" / f"{stem}.cs").is_file():
        return game / "scripts"
    return game / "scripts"


def _lml_version_guess(staging: Path) -> str | None:
    for path in staging.rglob("*"):
        match = re.search(r"lml_rdr_beta_([0-9_]+)", path.name, re.I)
        if match:
            return "beta " + match.group(1).replace("_", ".")
    return None


def _script_hook_url(page_html: str) -> tuple[str, str] | None:
    matches = re.findall(r'href="(/files/(ScriptHookRDR2_(\d+(?:\.\d+)+)\.zip))"', page_html)
    best: tuple[tuple, str, str] | None = None
    for path, _filename, version in matches:
        if "SDK" in path:
            continue
        key = version_key(version)
        if best is None or key > best[0]:
            best = (key, "https://www.dev-c.com" + path, version)
    if best is None:
        return None
    return best[1], best[2]


def _pick_ual_asset(assets: list[dict]) -> dict | None:
    zips = [asset for asset in assets if str(asset.get("name", "")).lower().endswith(".zip")]

    def score(asset: dict) -> tuple:
        name = asset.get("name", "").lower()
        value = 0
        if "x64" in name or "win64" in name:
            value += 10
        if "x86" in name or "win32" in name:
            value -= 20
        if "nopdb" in name:
            value += 3
        return (value, -int(asset.get("size") or 0))

    return max(zips, key=score) if zips else None


def _pick_dotnet_asset(assets: list[dict]) -> dict | None:
    files = [
        asset for asset in assets
        if str(asset.get("name", "")).lower().endswith((".rar", ".zip", ".7z"))
        and "source" not in str(asset.get("name", "")).lower()
    ]
    return files[0] if files else None


def _find_loader_dll(folder: Path) -> Path | None:
    candidates = [
        path for path in folder.rglob("*.dll")
        if path.name.lower() in LOADER_NAMES or path.name.lower() == "dinput8.dll"
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: ("x64" not in str(path).lower(), path.name.lower() != "dinput8.dll", -path.stat().st_size))
    return candidates[0]


def install_loader(archive: Path, game: Path, loader_name: str, version: str, log: Log) -> None:
    if loader_name not in LOADER_NAMES:
        loader_name = "dinput8.dll"
    extract_to = cache_dir() / "extract" / f"ual-{sanitize_name(version)}"
    if extract_to.exists():
        shutil.rmtree(extract_to)
    extract_archive(archive, extract_to, log)
    payload = _find_loader_dll(extract_to)
    if payload is None:
        raise RuntimeError("The ASI loader archive did not contain dinput8.dll.")
    if not is_pe64(payload):
        raise RuntimeError("The downloaded ASI loader is not a 64-bit DLL. RDR2 needs the x64 build.")
    other = "version.dll" if loader_name == "dinput8.dll" else "dinput8.dll"
    other_path = game / other
    manifest = read_manifest(game).get("installed", {}).get("asi_loader", {})
    if other_path.is_file() and manifest.get("file") == other and manifest.get("sha256") == sha256(other_path):
        _backup(game, other_path)
        other_path.unlink()
        log(f"Moved the previous {other} into _flashmanager_backup so plugins load once.")
    outcome = copy_file(payload, game / loader_name, game)
    digest = sha256(game / loader_name)
    update_manifest(game, "asi_loader", {"version": version, "file": loader_name, "sha256": digest, "source": "Ultimate ASI Loader"})
    if outcome == "same":
        log(f"Ultimate ASI Loader {version} is already in place as {loader_name}.")
    else:
        log(f"Placed Ultimate ASI Loader {version} as {loader_name}.")
    if (game / other).is_file():
        log(f"{other} is also in the folder. Remove it if the game crashes on startup. Two loaders will load every plugin twice.")


def _lml_links(page_html: str) -> list[tuple[tuple, str, str]]:
    found = []
    for match in re.finditer(r'href="([^"]*lml_rdr_beta_([0-9_]+)\.zip[^"]*)"', page_html, re.I):
        href, numbers = match.group(1), match.group(2)
        url = urllib.parse.urljoin(LML_PAGE, href)
        host = urllib.parse.urlparse(url).hostname
        if host not in ALLOWED_HOSTS:
            continue
        found.append((version_key(numbers.replace("_", ".")), url, "beta " + numbers.replace("_", ".")))
    return sorted(found, reverse=True)


def try_download_lml(log: Log) -> Path | None:
    try:
        html = fetch_text(LML_PAGE)
    except RuntimeError as exc:
        log(f"Could not open the Lenny's Mod Loader page. {exc}")
        return None
    if "data-pow" in html or "Checking your browser" in html:
        log("Lenny's site asks the browser to prove it is a person, so the download has to start there.")
        return None
    links = _lml_links(html)
    if not links:
        log("The Lenny's Mod Loader page did not list a zip FlashManager can fetch on its own.")
        return None
    _key, url, version = links[0]
    dest = cache_dir() / "downloads" / f"lml-{sanitize_name(version)}.zip"
    if not dest.is_file():
        log(f"Downloading Lenny's Mod Loader {version}.")
        download_file(url, dest, referer=LML_PAGE)
    return dest


def lml_zips_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name.lower()
        if not name.endswith(".zip"):
            continue
        compact = name.replace("'", "").replace(" ", "")
        if name.startswith("lml_rdr") or ("lenny" in compact and "loader" in compact):
            found.append(path)
    found.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return found


POPULAR_MODS = (
    {
        "id": "chaos",
        "name": "Chaos Mod RDR",
        "author": "clixff",
        "blurb": "A random story-mode effect every 45 seconds. Press F7. Twitch voting is optional.",
        "repo": "clixff/ChaosModRDR",
        "include": "chaosmodrdr_",
        "exclude": "notwitch",
    },
    {
        "id": "redlua",
        "name": "RedLua",
        "author": "igor725",
        "blurb": "Write and run Lua scripts. Press F7, the same key Chaos Mod uses.",
        "repo": "igor725/RedLua",
        "include": "redluardr2",
        "exclude": "",
    },
    {
        "id": "vram",
        "name": "Begone VRAM Limit",
        "author": "EricPlayZ",
        "blurb": "Lets the graphics menu keep settings above the VRAM warning.",
        "repo": "EricPlayZ/BegoneVRAMLimitRDR2",
        "include": "begonevramlimit",
        "exclude": "",
    },
    {
        "id": "presence",
        "name": "Discord Rich Presence",
        "author": "root-cause",
        "blurb": "Shows your story-mode location and activity on Discord.",
        "repo": "root-cause/rdr2-discord-rpc",
        "include": "rdr2_rpc",
        "exclude": "",
    },
)

OVERLAY_SKIP_EXT = {".md", ".txt", ".pdf", ".url", ".pdb", ".ilk", ".log", ".lnk", ".nfo"}
OVERLAY_SKIP_NAMES = {"license", "copying", "readme"}


def popular_mod(mod_id: str) -> dict | None:
    for mod in POPULAR_MODS:
        if mod["id"] == mod_id:
            return mod
    return None


def popular_catalog() -> list[dict]:
    return [
        {"id": mod["id"], "name": mod["name"], "author": mod["author"], "blurb": mod["blurb"], "repo": mod["repo"]}
        for mod in POPULAR_MODS
    ]


def installed_mods(game: Path | None) -> dict:
    if game is None:
        return {}
    installed = read_manifest(game).get("installed", {})
    found = {}
    for key, info in installed.items():
        if isinstance(key, str) and key.startswith("mod:") and isinstance(info, dict):
            found[key[4:]] = info.get("version") or ""
    return found


def forget_manifest(game: Path, key: str) -> None:
    data = read_manifest(game)
    installed = data.get("installed")
    if isinstance(installed, dict) and key in installed:
        installed.pop(key, None)
        write_manifest(game, data)


def _claimed_by_popular(game: Path) -> set[str]:
    claimed = set()
    installed = read_manifest(game).get("installed", {})
    for key, info in installed.items():
        if not (isinstance(key, str) and key.startswith("mod:") and isinstance(info, dict)):
            continue
        for rel in info.get("files") or []:
            if isinstance(rel, str) and rel.strip():
                claimed.add(rel.replace("\\", "/").strip("/").lower())
    return claimed


def _covered(rel: str, claimed: set[str]) -> bool:
    key = rel.replace("\\", "/").strip("/").lower()
    if not key:
        return False
    if key in claimed:
        return True
    prefix = key + "/"
    return any(item.startswith(prefix) for item in claimed)


def _library_row(mod_id: str, name: str, kind: str, detail: str) -> dict:
    return {"id": mod_id, "name": name, "kind": kind, "detail": detail}


def mod_library(game: Path | None) -> list[dict]:
    """Mods Modify can take back out. Prep's own tools are left off this list."""
    if game is None or not (game / "RDR2.exe").is_file():
        return []
    claimed = _claimed_by_popular(game)
    rows = []
    lml = game / "lml"
    if lml.is_dir():
        for child in lml.iterdir():
            if child.name.lower() in JUNK_NAMES or child.name.startswith("."):
                continue
            rel = f"lml/{child.name}"
            if _covered(rel, claimed):
                continue
            rows.append(_library_row(f"lml:{child.name}", child.name, "LML", rel))
    for path in game.iterdir():
        if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".asi":
            continue
        if path.name.lower() in CORE_LEAVE or _covered(path.name, claimed):
            continue
        rows.append(_library_row(f"root:{path.name}", path.name, "ASI", path.name))
    scripts = game / "scripts"
    if scripts.is_dir():
        for path in scripts.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in {".dll", ".cs", ".asi"}:
                continue
            if path.name.lower() in JUNK_NAMES or path.name.lower() in CORE_LEAVE:
                continue
            rel = path.relative_to(scripts).as_posix()
            if _covered(f"scripts/{rel}", claimed):
                continue
            rows.append(_library_row(f"script:{rel}", path.name, "Script", f"scripts/{rel}"))
    rows.sort(key=lambda item: (item["kind"], item["name"].lower()))
    return rows


def _named_child(parent: Path, name: str) -> Path:
    if not name or name in {".", ".."} or name != Path(name).name or "/" in name or "\\" in name:
        raise RuntimeError("That mod is not in the game folder.")
    if not parent.is_dir():
        raise RuntimeError("That mod is not in the game folder.")
    matches = [child for child in parent.iterdir() if child.name == name]
    if len(matches) != 1:
        raise RuntimeError("That mod is not in the game folder.")
    child = matches[0]
    if child.is_symlink():
        raise RuntimeError("That mod is a link. Remove the link in the game folder.")
    try:
        resolved = child.resolve()
        resolved.relative_to(parent.resolve())
    except (OSError, ValueError) as exc:
        raise RuntimeError("That mod is not in the game folder.") from exc
    if resolved.parent != parent.resolve() and parent.resolve() not in resolved.parents:
        raise RuntimeError("That mod is not in the game folder.")
    return child


def _prune_empty_dirs(start: Path, game: Path) -> None:
    root = game.resolve()
    keep = {root, (game / "lml").resolve(), (game / "scripts").resolve(), (game / "ModManager").resolve()}
    current = start.resolve()
    while current not in keep and current != current.parent:
        try:
            current.relative_to(root)
        except ValueError:
            return
        if not current.is_dir() or any(current.iterdir()):
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _delete_backed_up(game: Path, path: Path) -> None:
    _backup(game, path)
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        if access_denied(exc):
            raise RuntimeError(permission_message(path)) from exc
        raise


def _owned_target(game: Path, rel: str) -> Path | None:
    try:
        parsed = safe_rel(rel)
    except ValueError:
        return None
    if parsed.name.lower() in CORE_LEAVE or _forbidden(parsed):
        return None
    target = game.joinpath(parsed)
    if target.is_symlink():
        return None
    try:
        resolved = target.resolve()
        resolved.relative_to(game.resolve())
    except (OSError, ValueError):
        return None
    return target


def _remove_sibling(game: Path, path: Path, suffix: str, log: Log) -> None:
    sibling = path.with_suffix(suffix)
    if sibling == path or not sibling.is_file() or sibling.is_symlink():
        return
    if sibling.name.lower() in CORE_LEAVE or sibling.parent.resolve() != path.parent.resolve():
        return
    _delete_backed_up(game, sibling)
    log(f"Removed {sibling.name}.")


def _files_from_cached_release(mod_id: str, version: str) -> list[str]:
    if popular_mod(mod_id) is None or not version:
        return []
    folder = cache_dir() / "downloads"
    if not folder.is_dir():
        return []
    prefix = f"{mod_id}-{sanitize_name(version)}-".lower()
    matches = [path for path in folder.iterdir() if path.is_file() and path.name.lower().startswith(prefix)]
    if not matches:
        return []
    archive = max(matches, key=lambda path: path.stat().st_mtime)
    staging = cache_dir() / "staging" / f"remove-{sanitize_name(mod_id)}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        if archive.suffix.lower() in {".zip", ".rar", ".7z"}:
            extract_archive(archive, staging, Log())
        else:
            shutil.copy2(archive, staging / archive.name)
    except (RuntimeError, OSError):
        return []
    return overlay_files(staging)


def _remove_popular(game: Path, mod_id: str, log: Log) -> None:
    info = (read_manifest(game).get("installed") or {}).get(f"mod:{mod_id}")
    if not isinstance(info, dict):
        raise RuntimeError("That mod is not installed.")
    name = str(info.get("name") or mod_id)
    listed = info.get("files") if isinstance(info.get("files"), list) else []
    files = [item for item in listed if isinstance(item, str) and item.strip()]
    if not files:
        files = _files_from_cached_release(mod_id, str(info.get("version") or ""))
    if not files:
        raise RuntimeError(
            f"FlashManager does not have a list of files for {name}. "
            "Install it again from Popular mods, then remove it."
        )
    log(f"Removing {name}.")
    for rel in files:
        target = _owned_target(game, rel)
        if target is None:
            log(f"Left {rel} in place. That file belongs to the game.")
            continue
        if not target.exists():
            continue
        parent = target.parent
        _delete_backed_up(game, target)
        _prune_empty_dirs(parent, game)
        log(f"Removed {rel}.")
    forget_manifest(game, f"mod:{mod_id}")
    log.progress(100)
    log(f"Removed {name}. The previous files are in _flashmanager_backup.")


def _remove_lml(game: Path, name: str, log: Log) -> None:
    target = _named_child(game / "lml", name)
    label = target.name
    log(f"Removing {label}.")
    _delete_backed_up(game, target)
    log.progress(100)
    log(f"Removed {label}. The previous copy is in _flashmanager_backup.")


def _remove_root_file(game: Path, name: str, log: Log) -> None:
    if name.lower() in CORE_LEAVE:
        raise RuntimeError("That file is part of the story-mode setup, so Modify will not remove it.")
    target = _named_child(game, name)
    if target.suffix.lower() != ".asi" or not target.is_file():
        raise RuntimeError("That file is not a mod FlashManager can remove.")
    if target.name.lower() in CORE_LEAVE:
        raise RuntimeError("That file is part of the story-mode setup, so Modify will not remove it.")
    log(f"Removing {target.name}.")
    _delete_backed_up(game, target)
    _remove_sibling(game, target, ".ini", log)
    _remove_sibling(game, target, ".dll", log)
    log.progress(100)
    log(f"Removed {target.name}. The previous copy is in _flashmanager_backup.")


def _remove_script(game: Path, rel: str, log: Log) -> None:
    try:
        parsed = safe_rel(rel)
    except ValueError as exc:
        raise RuntimeError("That mod is not in the game folder.") from exc
    scripts = game / "scripts"
    target = scripts.joinpath(parsed)
    try:
        target.resolve().relative_to(scripts.resolve())
    except (OSError, ValueError) as exc:
        raise RuntimeError("That mod is not in the game folder.") from exc
    if target.is_symlink():
        raise RuntimeError("That mod is a link. Remove the link in the game folder.")
    if not target.is_file():
        raise RuntimeError("That mod is not in the game folder.")
    if target.suffix.lower() not in {".dll", ".cs", ".asi"} or target.name.lower() in CORE_LEAVE:
        raise RuntimeError("That file is not a mod FlashManager can remove.")
    # The lexical path has to stay inside scripts even when the file is missing a parent.
    if scripts.resolve() not in target.resolve().parents:
        raise RuntimeError("That mod is not in the game folder.")
    log(f"Removing {target.name}.")
    parent = target.parent
    _delete_backed_up(game, target)
    _remove_sibling(game, target, ".ini", log)
    _prune_empty_dirs(parent, game)
    log.progress(100)
    log(f"Removed scripts/{parsed.as_posix()}. The previous copy is in _flashmanager_backup.")


def remove_mod(game: Path, mod_id: str, log: Log) -> None:
    """Take one mod out of the game folder. The previous copy is kept in _flashmanager_backup."""
    if not (game / "RDR2.exe").is_file():
        raise RuntimeError("Choose the folder that contains RDR2.exe.")
    if game_is_running():
        raise RuntimeError("Close Red Dead Redemption 2 first. The game locks its files while it is open.")
    ensure_game_writable(game)
    _remove_mod_files(game, mod_id, log)


def _remove_mod_files(game: Path, mod_id: str, log: Log) -> None:
    kind, sep, ident = (mod_id or "").partition(":")
    if not sep or not ident:
        raise RuntimeError("That mod is not in the game folder.")
    if kind == "popular":
        _remove_popular(game, ident, log)
    elif kind == "lml":
        _remove_lml(game, ident, log)
    elif kind == "root":
        _remove_root_file(game, ident, log)
    elif kind == "script":
        _remove_script(game, ident, log)
    else:
        raise RuntimeError("That mod is not in the game folder.")


def pick_popular_asset(assets: list[dict], include: str = "", exclude: str = "") -> dict | None:
    chosen = []
    for asset in assets:
        name = str(asset.get("name") or "")
        lower = name.lower()
        if not lower.endswith((".zip", ".rar", ".7z", ".asi", ".dll")):
            continue
        if "source" in lower:
            continue
        if include and include.lower() not in lower:
            continue
        if exclude and exclude.lower() in lower:
            continue
        chosen.append(asset)
    return chosen[0] if chosen else None


def latest_release(repo: str) -> dict:
    try:
        release = json.loads(fetch_text(f"https://api.github.com/repos/{repo}/releases/latest"))
    except RuntimeError:
        release = None
    if isinstance(release, dict) and release.get("tag_name"):
        return release
    listed = json.loads(fetch_text(f"https://api.github.com/repos/{repo}/releases?per_page=10"))
    if not isinstance(listed, list) or not listed:
        raise RuntimeError(f"{repo} has no release FlashManager can download.")
    stable = [item for item in listed if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease")]
    pool = stable or [item for item in listed if isinstance(item, dict)]
    if not pool:
        raise RuntimeError(f"{repo} has no release FlashManager can download.")
    return pool[0]


def _overlay_root(staging: Path) -> Path:
    entries = [
        path for path in staging.iterdir()
        if path.name.lower() not in JUNK_NAMES and not path.name.startswith(".")
    ]
    dirs = [path for path in entries if path.is_dir()]
    files = [path for path in entries if path.is_file()]
    if len(dirs) == 1 and not files:
        return dirs[0]
    return staging


def _overlay_decision(path: Path, rel: Path) -> str | None:
    """None keeps the file. 'refuse' and 'drop' both skip it."""
    if path.name.lower() in JUNK_NAMES or path.suffix.lower() in OVERLAY_SKIP_EXT:
        return "drop"
    if path.stem.lower() in OVERLAY_SKIP_NAMES:
        return "drop"
    if path.name.lower() in PROTECTED or _forbidden(rel):
        return "refuse"
    if rel.parts and rel.parts[0].lower() in FORBIDDEN_TOP:
        return "drop"
    return None


def overlay_files(staging: Path) -> list[str]:
    """Relative paths install_overlay would copy into the game folder."""
    root = _overlay_root(staging)
    found = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _overlay_decision(path, rel) is not None:
            continue
        found.append(rel.as_posix())
    return found


def install_overlay(staging: Path, game: Path, log: Log, label: str) -> list[str]:
    """Copy a known mod onto the game folder, keeping its own folders.

    Returns the relative paths that now belong to the mod, so Modify can remove them later.
    """
    root = _overlay_root(staging)
    owned: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        decision = _overlay_decision(path, rel)
        if decision == "refuse":
            log(f"Refused {path.name}. That file belongs to the game.")
            continue
        if decision == "drop":
            continue
        _warn_machine(path, log)
        outcome = copy_file(path, game / rel, game)
        if outcome == "blocked":
            log(f"Refused {rel.as_posix()}.")
            continue
        owned.append(rel.as_posix())
        if path.suffix.lower() == ".exe":
            log(f"Placed {rel.as_posix()}. FlashManager did not run it.")
        elif outcome != "same":
            log(f"Placed {rel.as_posix()}.")
    if not owned:
        raise RuntimeError(f"{label} did not contain any files to install.")
    return owned


def install_popular_mod(game: Path, mod_id: str, log: Log) -> None:
    mod = popular_mod(mod_id)
    if mod is None:
        raise RuntimeError("That mod is not in the list.")
    if not (game / "RDR2.exe").is_file():
        raise RuntimeError("Choose the folder that contains RDR2.exe.")
    if game_is_running():
        raise RuntimeError("Close Red Dead Redemption 2 first. The game locks its files while it is open.")
    ensure_game_writable(game)
    log.progress(8)
    log(f"Checking {mod['name']}.")
    release = latest_release(mod["repo"])
    version = str(release.get("tag_name") or "latest")
    asset = pick_popular_asset(release.get("assets") or [], mod.get("include") or "", mod.get("exclude") or "")
    if asset is None:
        raise RuntimeError(f"{mod['name']} {version} has no file FlashManager can install.")
    filename = str(asset.get("name") or "download")
    dest = cache_dir() / "downloads" / f"{mod['id']}-{sanitize_name(version)}-{sanitize_name(filename)}"
    url = str(asset.get("browser_download_url") or "")
    if not dest.is_file():
        log(f"Downloading {mod['name']} {version}.")
        log.progress(25)
        download_file(url, dest)
    staging = cache_dir() / "staging" / f"mod-{mod['id']}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    log.progress(70)
    if dest.suffix.lower() in {".zip", ".rar", ".7z"}:
        extract_archive(dest, staging, log)
    else:
        shutil.copy2(dest, staging / filename)
    files = install_overlay(staging, game, log, mod["name"])
    update_manifest(game, f"mod:{mod['id']}", {
        "version": version,
        "name": mod["name"],
        "source": mod["repo"],
        "files": files,
    })
    if not (game / "ScriptHookRDR2.dll").is_file():
        log("Script Hook is not installed yet. Run Prep, or this mod will not load.")
    log.progress(100)
    log(f"Installed {mod['name']} {version}. Story mode only. Do not play Red Dead Online with these files installed.")


def run_prep(game: Path, options: dict, log: Log, wait_for_lml) -> None:
    """Download and install the story-mode essentials."""
    if not (game / "RDR2.exe").is_file():
        raise RuntimeError("Choose the folder that contains RDR2.exe.")
    if game_is_running():
        raise RuntimeError("Close Red Dead Redemption 2 first. The game locks its files while it is open.")
    ensure_game_writable(game)
    ensure_dir(game / "lml")
    ensure_dir(game / "scripts")
    force = bool(options.get("force"))
    loader_name = options.get("loader_name") if options.get("loader_name") in LOADER_NAMES else "dinput8.dll"
    manifest = read_manifest(game).get("installed", {})

    if options.get("asi_loader", True):
        log.progress(8)
        log("Checking Ultimate ASI Loader.")
        release = json.loads(fetch_text(UAL_API))
        version = release.get("tag_name") or "latest"
        asset = _pick_ual_asset(release.get("assets") or [])
        if asset is None:
            raise RuntimeError("The ASI loader release has no Windows zip.")
        local = (manifest.get("asi_loader") or {})
        current = local.get("version") == version and local.get("file") == loader_name and (game / loader_name).is_file()
        if current and not force:
            log(f"Ultimate ASI Loader {version} is already current ({loader_name}).")
        else:
            dest = cache_dir() / "downloads" / f"ual-{sanitize_name(version)}.zip"
            if not dest.is_file() or not looks_like_zip(dest):
                log(f"Downloading Ultimate ASI Loader {version}.")
                download_file(asset["browser_download_url"], dest)
            install_loader(dest, game, loader_name, version, log)
        log.progress(30)

    if options.get("script_hook", True):
        log("Checking Script Hook RDR2.")
        page = fetch_text(SCRIPT_HOOK_PAGE)
        located = _script_hook_url(page)
        if located is None:
            raise RuntimeError("Alexander Blade's page did not list a Script Hook download.")
        url, version = located
        local = manifest.get("script_hook") or {}
        current = local.get("version") == version and (game / "ScriptHookRDR2.dll").is_file()
        if current and not force:
            log(f"Script Hook RDR2 {version} is already current.")
        else:
            dest = cache_dir() / "downloads" / f"ScriptHookRDR2_{version}.zip"
            if not dest.is_file() or not looks_like_zip(dest):
                log(f"Downloading Script Hook RDR2 {version}.")
                download_file(url, dest, referer=SCRIPT_HOOK_PAGE)
            staging = cache_dir() / "staging" / "script-hook"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            shutil.copy2(dest, staging / dest.name)
            place_tree(
                staging,
                game,
                log,
                include_native_trainer=bool(options.get("native_trainer")),
                loader_name=loader_name,
            )
            if not (game / "ScriptHookRDR2.dll").is_file():
                raise RuntimeError("Script Hook downloaded, but ScriptHookRDR2.dll was not in the archive.")
            update_manifest(game, "script_hook", {"version": version, "file": "ScriptHookRDR2.dll", "source": "dev-c.com"})
        log.progress(55)

    if options.get("dotnet", True):
        log("Checking ScriptHookRDR2 .NET V2.")
        release = json.loads(fetch_text(DOTNET_API))
        version = release.get("tag_name") or "latest"
        asset = _pick_dotnet_asset(release.get("assets") or [])
        if asset is None:
            raise RuntimeError("The ScriptHookRDR2 .NET release has no archive.")
        local = manifest.get("dotnet") or {}
        ready = all((game / name).is_file() for name in DOTNET_FILES)
        if local.get("version") == version and ready and not force:
            log(f"ScriptHookRDR2 .NET {version} is already current.")
        else:
            filename = asset["name"]
            dest = cache_dir() / "downloads" / f"dotnet-{sanitize_name(version)}-{filename}"
            if not dest.is_file():
                log(f"Downloading ScriptHookRDR2 .NET {version}.")
                download_file(asset["browser_download_url"], dest)
            staging = cache_dir() / "staging" / "dotnet"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            shutil.copy2(dest, staging / dest.name)
            place_tree(staging, game, log, include_native_trainer=False, loader_name=loader_name)
            if not all((game / name).is_file() for name in DOTNET_FILES):
                raise RuntimeError("ScriptHookRDR2 .NET downloaded, but the ASI and API DLL were not both in the archive.")
            update_manifest(game, "dotnet", {"version": version, "files": list(DOTNET_FILES), "source": "Halen84"})
        log.progress(75)

    if options.get("lml", True):
        ready = (game / "vfs.asi").is_file() and (game / "lml.ini").is_file()
        downloaded = None
        if not (ready and not force):
            downloaded = try_download_lml(log)
        if downloaded is None and not ready:
            log("Waiting for the Lenny's Mod Loader zip.")
            log.progress(82)
            chosen = wait_for_lml()
            if chosen:
                downloaded = Path(chosen)
            elif (game / "vfs.asi").is_file() and (game / "lml.ini").is_file():
                log("Lenny's Mod Loader is in place.")
            else:
                log("Lenny's Mod Loader was not installed. Drop lml_rdr_beta_xx.zip on the pad when you have it.")
        elif downloaded is None and ready:
            log("Lenny's Mod Loader is already installed. Drop a newer lml_rdr zip here to update it.")
        if downloaded is not None:
            staging = cache_dir() / "staging" / "lml"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            shutil.copy2(downloaded, staging / downloaded.name)
            before = (game / "vfs.asi").stat().st_mtime if (game / "vfs.asi").is_file() else None
            place_tree(staging, game, log, include_native_trainer=False, loader_name=loader_name)
            if not (game / "vfs.asi").is_file():
                raise RuntimeError("That zip did not contain Lenny's Mod Loader (ModLoader\\vfs.asi).")
            version = _lml_version_guess(staging) or "installed"
            update_manifest(game, "lml", {"version": version, "source": "rdr2mods.com"})
            if before is not None and (game / "vfs.asi").stat().st_mtime == before and ready:
                log(f"Lenny's Mod Loader {version} is already in place.")
        log.progress(100)

    hook_version = file_version(game / "ScriptHookRDR2.dll")
    game_version = file_version(game / "RDR2.exe")
    if hook_version and game_version and version_key(game_version)[:3] != version_key(hook_version)[:3]:
        log(
            f"The game is {game_version} and Script Hook is {hook_version}. "
            "If scripts do not load after a Rockstar update, run Prep again once Script Hook is updated."
        )
    log.progress(100)
    log("Prep finished. Launch story mode from the Rockstar Launcher, Steam, or Epic. Do not play Red Dead Online with these files installed.")
