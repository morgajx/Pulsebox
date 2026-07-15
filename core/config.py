"""
PulseBox Config Manager & Module API
"""

import json
import os
from pathlib import Path


CONFIG_PATH = os.path.expanduser("~/.pulsebox_config.json")

DEFAULTS = {
    "music_dir": os.path.expanduser("~/Music"),
    "volume": 80,
    "theme": "dark",
    "kiosk_mode": False,
    "crossfade_seconds": 0,
    "shuffle": False,
    "repeat": "none",          # none | track | queue
    "columns_per_row": 6,
    "browse_mode": "covers",   # covers | tree
    "vlc_aout": "pipewire",        # pipewire | alsa | pulse | ""
    "vlc_alsa_device": "",         # e.g. hw:1,0 for HiFiBerry on card 1
    "start_letter": "A",       # default A-Z letter on startup
    "last_album_key": "",      # key of last played album
    "last_album_letter": "A",  # letter of last played album
}


class ConfigManager:
    def __init__(self, path: str = CONFIG_PATH):
        self._path = path
        self._data: dict = {}
        self.load()

    def load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def save(self):
        merged = {**DEFAULTS, **self._data}
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    def all(self) -> dict:
        return {**DEFAULTS, **self._data}


# ---------------------------------------------------------------------------
# Module / Plugin API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Single version constant — update this one value to bump the whole app
# ---------------------------------------------------------------------------
APP_VERSION = "0.273"

class JukeboxModule:
    """
    Base class for all PulseBox modules.

    To create a module:
        1. Subclass JukeboxModule (PulseBox module base)
        2. Set MODULE_ID, MODULE_NAME, MODULE_VERSION
        3. Override on_load(), on_unload(), get_ui_panel() as needed
        4. Drop the file in the /modules directory

    The module receives the app context (player, library, config) via on_load().
    """

    MODULE_ID: str = "base_module"          # unique snake_case identifier
    MODULE_NAME: str = "Base Module"        # human-readable name
    MODULE_VERSION: str = APP_VERSION
    MODULE_DESCRIPTION: str = "No description."
    MODULE_AUTHOR: str = "Unknown"

    def __init__(self):
        self.player = None
        self.library = None
        self.config = None
        self.app = None          # reference to the main App object
        self._enabled = False

    # ------------------------------------------------------------------
    # Lifecycle hooks – override in subclass
    # ------------------------------------------------------------------

    def on_load(self, app):
        """Called when the module is loaded. Store references here."""
        self.app = app
        self.player = app.player
        self.library = app.library
        self.config = app.config
        self._enabled = True

    def on_unload(self):
        """Called before the module is disabled/removed."""
        self._enabled = False

    def on_track_started(self, track: dict):
        """Called each time a new track begins playing."""

    def on_track_ended(self):
        """Called when a track finishes."""

    def on_queue_empty(self):
        """Called when the play queue is exhausted."""

    # ------------------------------------------------------------------
    # UI integration – override to add a panel to the sidebar/modules tab
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> object | None:
        """
        Return a Tkinter widget to embed in the Modules tab.
        Return None if this module has no UI.
        """
        return None

    def get_menu_items(self) -> list[dict]:
        """
        Return a list of menu items to inject into the main menu.
        Each item: {"label": str, "command": callable}
        """
        return []

    # ------------------------------------------------------------------
    # Utility helpers available to all modules
    # ------------------------------------------------------------------

    def emit_notification(self, message: str, level: str = "info"):
        """Show a toast/notification in the main UI."""
        if self.app and hasattr(self.app, "show_notification"):
            self.app.show_notification(message, level)

    @property
    def is_enabled(self) -> bool:
        return self._enabled


class ModuleRegistry:
    """Discovers, loads, and manages JukeBox modules."""

    def __init__(self, modules_dir: str = "modules"):
        self._dir = modules_dir
        self._modules: dict[str, JukeboxModule] = {}

    def discover(self) -> list[str]:
        """Scan modules directory and return importable module IDs."""
        found = []
        for p in Path(self._dir).glob("*.py"):
            if not p.name.startswith("_"):
                found.append(p.stem)
        return found

    def load_module(self, module_stem: str, app) -> JukeboxModule | None:
        import importlib.util, sys
        path = Path(self._dir) / f"{module_stem}.py"
        if not path.exists():
            print(f"[Modules] {path} not found")
            return None
        try:
            spec = importlib.util.spec_from_file_location(module_stem, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_stem] = mod
            spec.loader.exec_module(mod)
            # Find JukeboxModule subclass
            for attr in dir(mod):
                cls = getattr(mod, attr)
                if (isinstance(cls, type)
                        and issubclass(cls, JukeboxModule)
                        and cls is not JukeboxModule):
                    instance = cls()
                    instance.on_load(app)
                    self._modules[instance.MODULE_ID] = instance
                    print(f"[Modules] Loaded: {instance.MODULE_NAME} v{instance.MODULE_VERSION}")
                    return instance
        except Exception as e:
            print(f"[Modules] Failed to load {module_stem}: {e}")
        return None

    def load_all(self, app):
        for stem in self.discover():
            self.load_module(stem, app)

    def get_module(self, module_id: str) -> JukeboxModule | None:
        return self._modules.get(module_id)

    def get_all(self) -> list[JukeboxModule]:
        return list(self._modules.values())

    def unload_module(self, module_id: str):
        m = self._modules.pop(module_id, None)
        if m:
            m.on_unload()

    def broadcast(self, event: str, *args):
        """Fire an event on all loaded modules."""
        for m in self._modules.values():
            handler = getattr(m, f"on_{event}", None)
            if callable(handler):
                try:
                    handler(*args)
                except Exception as e:
                    print(f"[Modules] {m.MODULE_ID}.on_{event} error: {e}")
