"""
PulseBox Playlist Manager

Implements two distinct concepts:

1. SELECTION QUEUE  — the live jukebox queue of up to MAX_SELECTIONS
   individual track picks. When empty the player falls through to
   album-continuation mode (plays remaining tracks in the current album).

2. SAVED PLAYLISTS  — named collections stored in ~/.pulsebox_playlists.json
   that can be loaded back into the selection queue at any time.
"""

import json
import os
import time
from typing import Callable

MAX_SELECTIONS = 25
PLAYLISTS_PATH = os.path.expanduser("~/.pulsebox_playlists.json")


# ---------------------------------------------------------------------------
# Selection entry
# ---------------------------------------------------------------------------

class Selection:
    """One track picked by the user for the jukebox queue."""

    def __init__(self, track: dict):
        self.track     = dict(track)
        self.added_at  = time.time()
        self.played    = False

    def to_dict(self) -> dict:
        return {
            "track":    self.track,
            "added_at": self.added_at,
            "played":   self.played,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Selection":
        s = cls(d["track"])
        s.added_at = d.get("added_at", time.time())
        s.played   = d.get("played", False)
        return s


# ---------------------------------------------------------------------------
# Playlist Manager
# ---------------------------------------------------------------------------

class PlaylistManager:
    """
    Manages the live selection queue and saved playlists.

    Events emitted via on_event():
        queue_changed()          — selection queue was modified
        playback_mode_changed(mode)  — 'selections' | 'album_continuation'
    """

    MODE_SELECTIONS        = "selections"
    MODE_ALBUM_CONTINUATION = "album_continuation"

    def __init__(self, library, player):
        self._library    = library
        self._player     = player
        self._selections: list[Selection] = []
        self._mode       = self.MODE_ALBUM_CONTINUATION
        self._callbacks: dict[str, list[Callable]] = {}
        self._saved: dict[str, dict] = {}   # name -> {name, tracks, created}
        self._current_album_tracks: list[dict] = []
        self._album_index: int = 0

        # Always defer autoplay — tracks only start when the user
        # explicitly presses Play (playlist page) or Play All (album page).
        # This prevents ＋ (add to queue) from starting playback immediately.
        self.defer_autoplay: bool = True

        self._load_saved_playlists()
        self._wire_player()

    # ------------------------------------------------------------------
    # Event system
    # ------------------------------------------------------------------

    def on_event(self, event: str, cb: Callable):
        self._callbacks.setdefault(event, []).append(cb)

    def _emit(self, event: str, *args):
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception as e:
                print(f"[Playlist] Callback error ({event}): {e}")

    # ------------------------------------------------------------------
    # Selection Queue API
    # ------------------------------------------------------------------

    def start_playback(self):
        """
        Explicitly start playback of the pending queue.
        Called by the Play button / video overlay sequence.
        """
        from core.player import PlayerState
        state = self._player.get_state()
        if state in (PlayerState.STOPPED, PlayerState.PAUSED):
            # Always start the next queued track fresh —
            # never resume from a mid-track pause position
            self._player.stop()
            self._play_next_selection()

    def add_selection(self, track: dict) -> tuple[bool, str]:
        """
        Add a track to the jukebox selection queue.
        Returns (success, message).
        """
        if len(self._selections) >= MAX_SELECTIONS:
            return False, f"Queue full ({MAX_SELECTIONS} max)"

        # Prevent exact duplicate consecutive entries
        pending = [s for s in self._selections if not s.played]
        if pending and pending[-1].track.get("id") == track.get("id"):
            return False, "Track already at end of queue"

        self._selections.append(Selection(track))
        self._update_mode()
        self._emit("queue_changed")

        # Auto-start playback only if not deferring autoplay.
        # defer_autoplay stays True until the user presses Play, so the
        # ＋ (add to queue) button never starts playback on its own.
        from core.player import PlayerState
        if (self._player.get_state() == PlayerState.STOPPED
                and not self.defer_autoplay):
            self._play_next_selection()

        remaining = MAX_SELECTIONS - len(self.pending_selections)
        return True, f"Added to queue ({remaining} slots remaining)"

    def remove_selection(self, index: int):
        """Remove a pending selection by its position in the pending list."""
        pending = self.pending_selections
        if 0 <= index < len(pending):
            sel = pending[index]
            self._selections.remove(sel)
            self._update_mode()
            self._emit("queue_changed")

    def clear_selections(self):
        self._selections = [s for s in self._selections if s.played]
        self._update_mode()
        self._emit("queue_changed")

    def clear_all(self):
        self._selections.clear()
        self._update_mode()
        self._emit("queue_changed")

    @property
    def pending_selections(self) -> list[Selection]:
        return [s for s in self._selections if not s.played]

    @property
    def played_selections(self) -> list[Selection]:
        return [s for s in self._selections if s.played]

    @property
    def selection_count(self) -> int:
        return len(self.pending_selections)

    @property
    def slots_remaining(self) -> int:
        return MAX_SELECTIONS - self.selection_count

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------
    # Album continuation
    # ------------------------------------------------------------------

    def set_album_context(self, tracks: list[dict], current_index: int):
        """
        Tell the playlist manager which album is currently active
        so it can continue playing remaining tracks when the queue empties.
        """
        self._current_album_tracks = list(tracks)
        self._album_index = current_index

    # ------------------------------------------------------------------
    # Saved Playlists API
    # ------------------------------------------------------------------

    def save_playlist(self, name: str) -> tuple[bool, str]:
        """Save current pending selections as a named playlist."""
        pending = self.pending_selections
        if not pending:
            return False, "Nothing in queue to save"
        if not name.strip():
            return False, "Name cannot be empty"

        self._saved[name] = {
            "name":    name,
            "tracks":  [s.track for s in pending],
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "count":   len(pending),
        }
        self._persist_saved_playlists()
        return True, f"Saved '{name}' ({len(pending)} tracks)"

    def load_playlist(self, name: str) -> tuple[bool, str]:
        """Load a saved playlist into the selection queue (appends)."""
        pl = self._saved.get(name)
        if not pl:
            return False, "Playlist not found"

        tracks = pl.get("tracks", [])
        added = 0
        for track in tracks:
            if self.selection_count >= MAX_SELECTIONS:
                break
            self._selections.append(Selection(track))
            added += 1

        self._update_mode()
        self._emit("queue_changed")

        from core.player import PlayerState
        if self._player.get_state() == PlayerState.STOPPED and added:
            self._play_next_selection()

        return True, f"Loaded '{name}' — {added} tracks added"

    def delete_playlist(self, name: str) -> bool:
        if name in self._saved:
            del self._saved[name]
            self._persist_saved_playlists()
            return True
        return False

    def rename_playlist(self, old_name: str, new_name: str) -> tuple[bool, str]:
        if old_name not in self._saved:
            return False, "Playlist not found"
        if not new_name.strip():
            return False, "Name cannot be empty"
        self._saved[new_name] = {**self._saved.pop(old_name), "name": new_name}
        self._persist_saved_playlists()
        return True, f"Renamed to '{new_name}'"

    def get_saved_playlists(self) -> list[dict]:
        return sorted(self._saved.values(), key=lambda p: p.get("created", ""))

    def get_playlist(self, name: str) -> dict | None:
        return self._saved.get(name)

    # ------------------------------------------------------------------
    # Internal playback wiring
    # ------------------------------------------------------------------

    def _wire_player(self):
        self._player.on_event("track_ended", self._on_track_ended)
        self._player.on_event("queue_empty", self._on_queue_empty)

    def _on_track_ended(self):
        """Called by player when a track finishes. Runs in player thread."""
        playing = self._player.get_current_track()
        if playing:
            for sel in self._selections:
                if (not sel.played
                        and sel.track.get("id") == playing.get("id")):
                    sel.played = True
                    break
        self._emit("queue_changed")

    def _on_queue_empty(self):
        """
        Player queue exhausted — decide what plays next.
        Runs in a fresh thread (dispatched by player) so safe to call VLC.
        """
        import threading
        threading.Thread(target=self._handle_queue_empty,
                         daemon=True).start()

    def _handle_queue_empty(self):
        """Actual queue-empty logic, safely off the VLC callback thread."""
        import time
        time.sleep(0.1)   # let VLC fully settle
        # Playback advances only after the user has pressed Play, which
        # clears defer_autoplay. The ＋ button leaves it set so adding a
        # track never auto-starts the queue.
        if self.defer_autoplay:
            return
        pending = self.pending_selections
        if pending:
            self._play_next_selection()
        else:
            self._continue_album()

    def _play_next_selection(self):
        pending = self.pending_selections
        if not pending:
            return
        track = pending[0].track
        self._player.set_queue([track], 0)
        self._update_mode()

    def _continue_album(self):
        """Play the next track in the current album."""
        tracks = self._current_album_tracks
        if not tracks:
            return

        current = self._player.get_current_track()
        idx     = self._album_index

        if current:
            for i, t in enumerate(tracks):
                if t.get("id") == current.get("id"):
                    idx = i
                    break

        next_idx = idx + 1
        if next_idx < len(tracks):
            self._album_index = next_idx
            self._player.set_queue(tracks, next_idx)

    def _update_mode(self):
        new_mode = (self.MODE_SELECTIONS if self.pending_selections
                    else self.MODE_ALBUM_CONTINUATION)
        if new_mode != self._mode:
            self._mode = new_mode
            self._emit("playback_mode_changed", new_mode)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_saved_playlists(self):
        try:
            with open(PLAYLISTS_PATH, "w", encoding="utf-8") as f:
                json.dump(self._saved, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Playlist] Save error: {e}")

    def _load_saved_playlists(self):
        try:
            if os.path.exists(PLAYLISTS_PATH):
                with open(PLAYLISTS_PATH, encoding="utf-8") as f:
                    self._saved = json.load(f)
        except Exception as e:
            print(f"[Playlist] Load error: {e}")
            self._saved = {}
