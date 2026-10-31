"""FlashManager — open a local page that readies Red Dead Redemption 2 for story-mode mods.

Double-click "Start FlashManager.bat". Leave the console window open while the page is in use.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import engine

TOOL = Path(__file__).resolve().parent
PAGE = TOOL / "web" / "index.html"
CREATE_NO_WINDOW = engine.CREATE_NO_WINDOW
SERVER: ThreadingHTTPServer | None = None

DEFAULT_OPTIONS = {
    "script_hook": True,
    "asi_loader": True,
    "lml": True,
    "dotnet": True,
    "native_trainer": False,
    "loader_name": "dinput8.dll",
    "force": False,
}


class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.work_lock = threading.Lock()
        self.options = dict(DEFAULT_OPTIONS)
        saved = engine.load_settings()
        for key in DEFAULT_OPTIONS:
            if key in saved:
                self.options[key] = saved[key]
        if self.options.get("loader_name") not in engine.LOADER_NAMES:
            self.options["loader_name"] = "dinput8.dll"
        self.game: Path | None = None
        remembered = saved.get("game_path")
        if remembered and (Path(remembered) / "RDR2.exe").is_file():
            self.game = Path(remembered)
        else:
            found = engine.discover_games(TOOL)
            if found:
                self.game = Path(found[0]["path"])
        self.save_profile: Path | None = None
        remembered_profile = saved.get("save_profile")
        if remembered_profile:
            profile = engine.normalize_profile_dir(Path(remembered_profile))
            if profile is not None:
                self.save_profile = profile
        self.job = {
            "running": False,
            "waiting": None,
            "progress": 0,
            "lines": [],
            "lml_candidate": None,
            "placed": [],
        }
        self.drops: dict[str, Path] = {}
        self.lml_event = threading.Event()
        self.lml_choice: Path | None = None
        self._found: list[dict] = []
        self._found_at = 0.0

    def candidates(self) -> list[dict]:
        now = time.time()
        if now - self._found_at > 12:
            self._found = engine.discover_games(TOOL)
            self._found_at = now
        return self._found

    def set_game(self, path: Path) -> dict:
        game = engine.normalize_game_dir(path)
        if game is None:
            raise RuntimeError("That folder does not contain RDR2.exe.")
        self.game = game
        engine.save_settings({"game_path": str(game)})
        self._found_at = 0.0
        return engine.scan_game(game)["game"]

    def profile_state(self) -> dict:
        profiles = engine.discover_profiles()
        known = {item["path"].lower(): item for item in profiles}
        selected = self.save_profile
        if selected is not None and str(selected).lower() not in known:
            if selected.is_dir() and engine.normalize_profile_dir(selected) is not None:
                profiles.append({
                    "id": selected.name,
                    "path": str(selected),
                    "slots": engine._profile_slots(selected),
                })
            else:
                selected = None
                self.save_profile = None
        if selected is None and len(profiles) == 1:
            selected = Path(profiles[0]["path"])
            self.save_profile = selected
            engine.save_settings({"save_profile": str(selected)})
        return {
            "profiles": profiles,
            "selected": str(selected) if selected else None,
            "root": str(engine.profiles_root()),
        }

    def set_profile(self, path: Path) -> dict:
        profile = engine.normalize_profile_dir(path)
        if profile is None:
            raise RuntimeError(
                "Choose the folder inside Documents\\Rockstar Games\\Red Dead Redemption 2\\Profiles."
            )
        self.save_profile = profile
        engine.save_settings({"save_profile": str(profile)})
        return {"id": profile.name, "path": str(profile), "slots": engine._profile_slots(profile)}

    def status(self) -> dict:
        found = self.candidates()
        with self.lock:
            job = {
                "running": self.job["running"],
                "waiting": self.job["waiting"],
                "progress": self.job["progress"],
                "lines": list(self.job["lines"]),
                "lml_candidate": self.job["lml_candidate"],
                "placed": list(self.job["placed"]),
            }
            options = dict(self.options)
            game = self.game
        scanned = engine.scan_game(game)
        writable = engine.game_is_writable(game) if game is not None else True
        return {
            "game": scanned.get("game"),
            "components": scanned["components"],
            "candidates": found,
            "options": options,
            "runtime": {
                "dotnet48": engine.dotnet48_installed(),
                "vcpp": engine.vcpp_x64_installed(),
            },
            "job": job,
            "saves": self.profile_state(),
            "mods": engine.installed_mods(game),
            "library": engine.mod_library(game),
            "needs_admin": game is not None and not writable and not engine.is_admin(),
            "write_blocked": game is not None and not writable,
        }

    def update_options(self, payload: dict) -> None:
        with self.lock:
            for key in DEFAULT_OPTIONS:
                if key in payload:
                    self.options[key] = payload[key]
            if self.options.get("loader_name") not in engine.LOADER_NAMES:
                self.options["loader_name"] = "dinput8.dll"
            snapshot = dict(self.options)
        engine.save_settings(snapshot)

    def start_prep(self) -> None:
        with self.lock:
            if self.job["running"]:
                raise RuntimeError("Prep is already running.")
            if self.game is None:
                raise RuntimeError("Choose the game folder first.")
            game = self.game
            options = dict(self.options)
            self.job.update({
                "running": True,
                "waiting": None,
                "progress": 1,
                "lines": [],
                "lml_candidate": None,
                "placed": [],
            })
        engine.save_settings(options)
        threading.Thread(target=self._prep, args=(game, options), daemon=True).start()

    def start_mod(self, mod_id: str) -> None:
        if engine.popular_mod(mod_id) is None:
            raise RuntimeError("That mod is not in the list.")
        with self.lock:
            if self.job["running"]:
                raise RuntimeError("FlashManager is already working. Wait for it to finish.")
            if self.game is None:
                raise RuntimeError("Choose the game folder first.")
            game = self.game
            self.job.update({
                "running": True,
                "waiting": None,
                "progress": 1,
                "lines": [],
                "lml_candidate": None,
                "placed": [],
            })
        threading.Thread(target=self._install_mod, args=(game, mod_id), daemon=True).start()

    def start_remove(self, mod_id: str) -> None:
        if ":" not in (mod_id or ""):
            raise RuntimeError("That mod is not in the game folder.")
        with self.lock:
            if self.job["running"]:
                raise RuntimeError("FlashManager is already working. Wait for it to finish.")
            if self.game is None:
                raise RuntimeError("Choose the game folder first.")
            game = self.game
            self.job.update({
                "running": True,
                "waiting": None,
                "progress": 1,
                "lines": [],
                "lml_candidate": None,
                "placed": [],
            })
        threading.Thread(target=self._remove_mod, args=(game, mod_id), daemon=True).start()

    def _remove_mod(self, game: Path, mod_id: str) -> None:
        reporter = Reporter(self)
        self.work_lock.acquire()
        try:
            engine.remove_mod(game, mod_id, reporter)
        except Exception as exc:
            reporter(f"Remove stopped. {_explain(exc)}")
            traceback.print_exc()
        finally:
            self.work_lock.release()
            with self.lock:
                self.job["running"] = False
                self.job["waiting"] = None
                if self.job["progress"] < 100:
                    self.job["progress"] = 100

    def _install_mod(self, game: Path, mod_id: str) -> None:
        reporter = Reporter(self)
        self.work_lock.acquire()
        try:
            engine.install_popular_mod(game, mod_id, reporter)
        except Exception as exc:
            reporter(f"Install stopped. {_explain(exc)}")
            traceback.print_exc()
        finally:
            self.work_lock.release()
            with self.lock:
                self.job["running"] = False
                self.job["waiting"] = None
                if self.job["progress"] < 100:
                    self.job["progress"] = 100

    def _prep(self, game: Path, options: dict) -> None:
        reporter = Reporter(self)
        self.work_lock.acquire()
        try:
            reporter("Preparing the story-mode folder.")
            engine.run_prep(game, options, reporter, self.wait_for_lml)
        except Exception as exc:
            reporter(f"Prep stopped. {_explain(exc)}")
            traceback.print_exc()
        finally:
            self.work_lock.release()
            with self.lock:
                self.job["running"] = False
                self.job["waiting"] = None
                if self.job["progress"] < 100:
                    self.job["progress"] = 100

    def wait_for_lml(self):
        """Block until a Lenny's Mod Loader zip shows up, or the drop pad installs it."""
        folder = engine.downloads_dir()
        baseline = {path.resolve() for path in engine.lml_zips_in(folder)}
        existing = engine.lml_zips_in(folder)
        self.lml_event.clear()
        self.lml_choice = None
        with self.lock:
            self.job["waiting"] = "lml"
            self.job["lml_candidate"] = existing[0].name if existing else None
        webbrowser.open(engine.LML_PAGE)
        print("Opened the Lenny's Mod Loader page.", flush=True)
        sizes: dict[Path, int] = {}
        self.work_lock.release()
        try:
            while not self.lml_event.wait(0.6):
                game = self.game
                if game and (game / "vfs.asi").is_file() and (game / "lml.ini").is_file():
                    return None
                for path in engine.lml_zips_in(folder):
                    if path.resolve() in baseline:
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if sizes.get(path) == size and size > 0:
                        return path
                    sizes[path] = size
                newest = engine.lml_zips_in(folder)
                with self.lock:
                    self.job["lml_candidate"] = newest[0].name if newest else None
            return self.lml_choice
        finally:
            self.work_lock.acquire()
            with self.lock:
                self.job["waiting"] = None

    def choose_existing_lml(self) -> None:
        found = engine.lml_zips_in(engine.downloads_dir())
        if not found:
            raise RuntimeError("No Lenny's Mod Loader zip is in your Downloads folder.")
        self.lml_choice = found[0]
        self.lml_event.set()

    def cancel_lml(self) -> None:
        self.lml_choice = None
        self.lml_event.set()


class Reporter(engine.Log):
    def __init__(self, app: App):
        super().__init__()
        self.app = app

    def __call__(self, message: str) -> None:
        print(message, flush=True)
        super().__call__(message)
        if len(self.lines) > 300:
            del self.lines[:-300]
        with self.app.lock:
            self.app.job["lines"] = list(self.lines)

    def progress(self, percent: int) -> None:
        super().progress(percent)
        with self.app.lock:
            self.app.job["progress"] = self.percent


APP = App()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise RuntimeError("That request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise RuntimeError("Expected a JSON object.")
        return data

    def _error(self, exc: Exception, code: int = 400) -> None:
        self._json(code, {"error": str(exc)})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(200, APP.status())
            return
        if path == "/api/mods":
            self._json(200, {"mods": engine.popular_catalog()})
            return
        self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/elevate":
                relaunch_elevated()
                self._json(200, {"ok": True})
                threading.Thread(target=_shutdown_soon, daemon=True).start()
                return
            if path == "/api/prep":
                payload = self._read_json()
                if payload:
                    APP.update_options(payload)
                APP.start_prep()
                self._json(200, {"ok": True})
                return
            if path == "/api/mods/install":
                APP.start_mod(str(self._read_json().get("id") or ""))
                self._json(200, {"ok": True})
                return
            if path == "/api/mods/remove":
                APP.start_remove(str(self._read_json().get("id") or ""))
                self._json(200, {"ok": True})
                return
            if path == "/api/options":
                APP.update_options(self._read_json())
                self._json(200, {"ok": True})
                return
            if path == "/api/game":
                raw = self._read_json().get("path") or ""
                game = APP.set_game(Path(raw))
                self._json(200, {"ok": True, "game": game})
                return
            if path == "/api/profile":
                raw = self._read_json().get("path") or ""
                profile = APP.set_profile(Path(raw))
                self._json(200, {"ok": True, "profile": profile})
                return
            if path == "/api/browse":
                picked = pick_folder()
                if not picked:
                    self._json(200, {"ok": True, "game": None})
                    return
                game = APP.set_game(Path(picked))
                self._json(200, {"ok": True, "game": game})
                return
            if path == "/api/lml":
                action = self._read_json().get("action")
                if action == "open":
                    webbrowser.open(engine.LML_PAGE)
                elif action == "use_existing":
                    APP.choose_existing_lml()
                elif action == "cancel":
                    APP.cancel_lml()
                else:
                    raise RuntimeError("Unknown action.")
                self._json(200, {"ok": True})
                return
            if path == "/api/open":
                target = self._read_json().get("target")
                if target == "saves":
                    open_saves(APP.save_profile)
                else:
                    open_target(APP.game, target)
                self._json(200, {"ok": True})
                return
            if path == "/api/drop/begin":
                drop_id = uuid.uuid4().hex
                folder = engine.cache_dir() / "drops" / drop_id
                folder.mkdir(parents=True, exist_ok=True)
                APP.drops[drop_id] = folder
                self._json(200, {"id": drop_id})
                return
            if path.startswith("/api/drop/") and path.endswith("/finish"):
                drop_id = path.split("/")[3]
                self._finish_drop(drop_id)
                return
        except RuntimeError as exc:
            self._error(exc)
            return
        except Exception as exc:
            traceback.print_exc()
            self._error(RuntimeError(_explain(exc)), 500)
            return
        self._json(404, {"error": "Not found."})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        parts = [part for part in path.split("/") if part]
        if len(parts) != 3 or parts[0] != "api" or parts[1] != "drop":
            self._json(404, {"error": "Not found."})
            return
        drop_id = parts[2]
        folder = APP.drops.get(drop_id)
        if folder is None:
            self._json(404, {"error": "That drop has expired. Drop the files again."})
            return
        try:
            rel = engine.safe_rel(self.headers.get("X-File-Path") or "")
        except ValueError as exc:
            self._error(exc)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 8 * 1024 * 1024 * 1024:
            self._error(RuntimeError("That file is larger than 8 GB."))
            return
        dest = folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        remaining = length
        try:
            with dest.open("wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            self._error(RuntimeError(f"Could not store {rel.as_posix()}. {exc}"))
            return
        self._json(200, {"ok": True})

    def _finish_drop(self, drop_id: str) -> None:
        folder = APP.drops.get(drop_id)
        if folder is None:
            self._json(404, {"error": "That drop has expired. Drop the files again."})
            return
        if engine.game_is_running():
            self._error(RuntimeError("Close Red Dead Redemption 2 first. The game locks its files while it is open."))
            return
        if not APP.work_lock.acquire(blocking=False):
            self._error(RuntimeError("FlashManager is still writing files. Try the drop again in a moment."), 409)
            return
        reporter = Reporter(APP)
        try:
            reporter("Sorting the drop.")
            if APP.game is None:
                result = engine.place_saves_only(folder, APP.save_profile, reporter)
            else:
                result = engine.place_tree(
                    folder,
                    APP.game,
                    reporter,
                    include_native_trainer=bool(APP.options.get("native_trainer", True)),
                    loader_name=APP.options.get("loader_name") or "dinput8.dll",
                    save_profile=APP.save_profile,
                )
            with APP.lock:
                APP.job["placed"] = result.placed
                APP.job["lines"] = list(reporter.lines)
            if not result.ok:
                message = reporter.lines[-1] if reporter.lines else "Could not place those files."
                self._error(RuntimeError(message))
                return
            self._json(200, {"ok": True, "placed": result.placed, "lines": reporter.lines})
        except Exception as exc:
            traceback.print_exc()
            self._error(RuntimeError(_explain(exc)), 500)
        finally:
            APP.work_lock.release()
            APP.drops.pop(drop_id, None)


def pick_folder() -> str | None:
    script = engine.cache_dir() / "pick-folder.ps1"
    script.write_text(
        "\n".join([
            "Add-Type -AssemblyName System.Windows.Forms",
            "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "$owner = New-Object System.Windows.Forms.Form",
            "$owner.TopMost = $true",
            "$owner.ShowInTaskbar = $false",
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
            "$dialog.Description = 'Select the folder that contains RDR2.exe'",
            "$dialog.ShowNewFolderButton = $false",
            "$picked = $dialog.ShowDialog($owner)",
            "$owner.Dispose()",
            "if ($picked -eq [System.Windows.Forms.DialogResult]::OK) {",
            "  Write-Output $dialog.SelectedPath",
            "}",
            "",
        ]),
        encoding="utf-8",
    )
    result = engine._run_hidden(["powershell", "-NoProfile", "-STA", "-File", str(script)])
    text = (result.stdout or "").strip()
    return text or None


def open_saves(profile: Path | None) -> None:
    if profile is not None and profile.is_dir():
        os.startfile(profile)  # type: ignore[attr-defined]
        return
    root = engine.profiles_root()
    if root.is_dir():
        os.startfile(root)  # type: ignore[attr-defined]
        return
    raise RuntimeError("No Rockstar profile yet. Launch story mode once and make a save.")


def open_target(game: Path | None, target: str) -> None:
    if game is None or not game.is_dir():
        raise RuntimeError("Choose the game folder first.")
    mapping = {
        "game": game,
        "lml": game / "lml",
        "scripts": game / "scripts",
        "manager": game / "ModManager",
        "backup": game / "_flashmanager_backup",
        "review": game / "_flashmanager_review",
    }
    folder = mapping.get(target or "")
    if folder is None:
        raise RuntimeError("Unknown folder.")
    if target in {"lml", "scripts"} and not folder.exists():
        engine.ensure_game_writable(game)
        engine.ensure_dir(folder)
    if not folder.exists():
        raise RuntimeError(f"{folder.name} is not there yet.")
    os.startfile(folder)  # type: ignore[attr-defined]


def _explain(exc: BaseException) -> str:
    if engine.access_denied(exc):
        return engine.permission_message()
    return str(exc)


def relaunch_elevated() -> None:
    """Ask Windows for an administrator copy of this program, then leave the old one running until the page answers."""
    if engine.is_admin():
        raise RuntimeError("FlashManager is already running as administrator.")
    script = str((TOOL / "flashmanager.py").resolve())
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}"', str(TOOL), 1)
    if int(result) <= 32:
        raise RuntimeError("The administrator prompt was closed. Allow it, then click Run as administrator again.")


def _shutdown_soon() -> None:
    time.sleep(0.7)
    server = SERVER
    if server is not None:
        server.shutdown()


def main() -> None:
    global SERVER
    os.chdir(TOOL)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    SERVER = server
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print("FlashManager is open in your browser.", flush=True)
    print(url, flush=True)
    print("Leave this window open. Close it when you are done.", flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFlashManager stopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
