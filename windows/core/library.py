"""
PulseBox Library Scanner — Normalised SQLite schema
=====================================================
Schema v5: properly normalised tables.

  tracks      — one row per physical file, UNIQUE on path
  albums      — one row per album
  album_tracks — junction: which tracks appear on which album + track number
  album_art   — BLOB cover art keyed by album_id
  meta        — key/value store for library metadata

A track (physical file) can appear on multiple albums (e.g. a studio album
AND a compilation) without any data duplication. The in-memory structures
returned by the public API are identical to the old schema so the UI,
playlist manager and web remote need no changes.
"""

import hashlib
import io
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Optional

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".wma"}
DB_PATH    = os.path.expanduser("~/.pulsebox_library.db")
DB_VERSION = 5
ART_SIZE   = 300   # pixels — cover art stored at this size


# ---------------------------------------------------------------------------
# Tag / art helpers (unchanged)
# ---------------------------------------------------------------------------

def _get_mutagen_tags(path: str) -> dict:
    try:
        from mutagen import File as MutagenFile
        f = MutagenFile(path, easy=True)
        if f is None:
            raise ValueError("unreadable")
        t = f.tags or {}
        return {
            "title":       str(t.get("title",       [Path(path).stem])[0]),
            "artist":      str(t.get("artist",       ["Unknown Artist"])[0]),
            "album":       str(t.get("album",        ["Unknown Album"])[0]),
            "tracknumber": str(t.get("tracknumber",  ["0"])[0]),
            "genre":       str(t.get("genre",        [""])[0]),
            "date":        str(t.get("date",         [""])[0]),
            "duration":    int(f.info.length) if hasattr(f, "info") else 0,
        }
    except Exception:
        return {
            "title": Path(path).stem, "artist": "Unknown Artist",
            "album": "Unknown Album", "tracknumber": "0",
            "genre": "", "date": "", "duration": 0,
        }


def _find_cover_file(album_dir: str) -> Optional[str]:
    preferred = ["cover", "folder", "album", "front", "artwork", "thumb"]
    try:
        entries = os.listdir(album_dir)
    except OSError:
        return None
    lower_map = {e.lower(): e for e in entries}
    for stem in preferred:
        for ext in (".jpg", ".jpeg", ".png"):
            if stem + ext in lower_map:
                return os.path.join(album_dir, lower_map[stem + ext])
    for name, orig in lower_map.items():
        if name.endswith((".jpg", ".jpeg", ".png")):
            return os.path.join(album_dir, orig)
    return None


def _load_art_blob_from_file(path: str) -> Optional[bytes]:
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        img = img.resize((ART_SIZE, ART_SIZE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"[Library] art blob error ({path}): {e}")
        return None


def _load_art_blob_from_tags(audio_path: str) -> Optional[bytes]:
    raw: Optional[bytes] = None
    ext = audio_path.lower()
    try:
        if ext.endswith(".mp3"):
            from mutagen.id3 import ID3
            tags = ID3(audio_path)
            for tag in tags.values():
                if tag.FrameID == "APIC":
                    raw = tag.data
                    break
        elif ext.endswith(".flac"):
            from mutagen.flac import FLAC
            audio = FLAC(audio_path)
            if audio.pictures:
                raw = audio.pictures[0].data
        elif ext.endswith((".m4a", ".aac")):
            from mutagen import File
            audio = File(audio_path)
            if audio and "covr" in audio:
                raw = bytes(audio["covr"][0])
        elif ext.endswith(".ogg"):
            import base64, struct
            from mutagen import File
            audio = File(audio_path)
            if audio and "metadata_block_picture" in audio:
                data   = base64.b64decode(audio["metadata_block_picture"][0])
                offset = 4
                mime_len = struct.unpack(">I", data[offset:offset+4])[0]
                offset  += 4 + mime_len
                desc_len = struct.unpack(">I", data[offset:offset+4])[0]
                offset  += 4 + desc_len + 16
                img_len  = struct.unpack(">I", data[offset:offset+4])[0]
                raw = data[offset+4: offset+4+img_len]
    except Exception as e:
        print(f"[Library] tag art error ({Path(audio_path).name}): {e}")

    if raw:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            img = img.resize((ART_SIZE, ART_SIZE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
        except Exception:
            return raw
    return None


def _track_id(path: str) -> str:
    """Stable unique ID for a physical file — MD5 of its absolute path."""
    return hashlib.md5(path.encode()).hexdigest()[:12]


def _norm_album(s: str) -> str:
    """Normalise album name for grouping — strips whitespace, lowercases."""
    return s.strip().lower()


# ---------------------------------------------------------------------------
# MusicLibrary
# ---------------------------------------------------------------------------

class MusicLibrary:
    """
    SQLite-backed music library with normalised schema (v5).

    Public API returns the same in-memory dicts as the old flat schema:

      album dict  : {name, artist, key, art (bytes|None), tracks, dir}
      track dict  : {id, path, title, artist, album, tracknumber,
                     genre, date, duration}

    All de-duplication happens at the DB level — a track (file) that appears
    on multiple albums is stored once in `tracks` and referenced via
    `album_tracks`. The in-memory _albums dict uses album_dir-scoped keys so
    a compilation in one directory never collides with a studio album in
    another directory, even if both have the same name.
    """

    def __init__(self, music_dir: str, db_path: str = DB_PATH):
        self.music_dir  = music_dir
        self._db_path   = db_path
        self._conn      = None

        # In-memory caches (rebuilt from DB on every scan / startup)
        self._tracks:    dict[str, dict] = {}   # track_id -> track dict
        self._albums:    dict[str, dict] = {}   # alb_key  -> album dict
        self._artists:   dict[str, dict] = {}   # artist   -> {name, albums}
        self._art_cache: dict[int, Optional[bytes]] = {}  # album_id -> blob

    # ------------------------------------------------------------------
    # Public API — identical signatures to old schema
    # ------------------------------------------------------------------

    def close(self):
        if self._conn:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.commit()
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def scan(self, force: bool = False, progress_cb=None):
        self._open_db()
        self._migrate_schema()
        if force:
            self._full_scan(progress_cb)
        else:
            stored_dir = self._db_get_meta("music_dir")
            if stored_dir != self.music_dir or not self._db_has_tracks():
                self._full_scan(progress_cb)
            else:
                self._load_from_db()
                self._incremental_sync(progress_cb)
        self._load_art_from_db()
        self._build_indexes()

    def get_artists(self):
        return sorted(self._artists.keys(), key=lambda x: x.upper())

    def get_albums_for_artist(self, artist):
        a = self._artists.get(artist, {})
        return sorted(a.get("albums", {}).values(),
                      key=lambda x: x.get("name", "").upper())

    def get_tracks_for_album(self, artist, album):
        key = f"{artist}::{album}"
        alb = self._albums.get(key, {})
        return sorted(alb.get("tracks", []),
                      key=lambda t: self._track_num(t.get("tracknumber", "0")))

    def get_all_albums(self):
        # Artist/Album order: group by artist, then album name within artist.
        # Compilations carry the artist "Various Artists", so they cluster
        # together under V rather than scattering by album title.
        return sorted(self._albums.values(),
                      key=lambda a: (a.get("artist", "").upper(),
                                     a.get("name", "").upper()))

    def get_albums_by_letter(self, letter):
        # Index every album by the first letter of its artist. Compilations
        # have artist "Various Artists", so they all appear under V instead
        # of being scattered under the first letter of each album title.
        result = []
        for alb in self._albums.values():
            artist = alb.get("artist", "")
            first = artist.upper()[0] if artist else "#"
            if not first.isalpha():
                first = "#"
            if first == letter.upper():
                result.append(alb)
        return sorted(result, key=lambda a: (a.get("artist", "").upper(),
                                             a.get("name", "").upper()))

    def get_alphabet_index(self):
        letters = set()
        for alb in self._albums.values():
            artist = alb.get("artist", "")
            first = artist.upper()[0] if artist else "#"
            letters.add(first if first.isalpha() else "#")
        out = sorted(l for l in letters if l.isalpha())
        if "#" in letters:
            out = ["#"] + out
        return out

    def get_track(self, track_id):
        return self._tracks.get(track_id)

    def search(self, query):
        q = query.lower()
        tracks = [t for t in self._tracks.values()
                  if q in t.get("title",  "").lower()
                  or q in t.get("artist", "").lower()
                  or q in t.get("album",  "").lower()]
        albums = [a for a in self._albums.values()
                  if q in a.get("name",   "").lower()
                  or q in a.get("artist", "").lower()]
        return {"tracks": tracks, "albums": albums}

    def get_stats(self):
        return {
            "total_tracks":  len(self._tracks),
            "total_artists": len(self._artists),
            "total_albums":  len(self._albums),
        }

    def get_db_path(self):
        return self._db_path

    def get_art_blob(self, artist: str, album: str) -> Optional[bytes]:
        """Return JPEG BLOB for an album by artist+name, or None."""
        key = f"{artist}::{album}".upper()
        for alb in self._albums.values():
            k = f"{alb.get('artist','')}::{alb.get('name','')}".upper()
            if k == key:
                blob = alb.get("art")
                return blob if isinstance(blob, (bytes, bytearray)) else None
        return None

    def refresh_artwork(self):
        if self._conn:
            self._conn.execute("DELETE FROM album_art")
            self._conn.commit()
        self._art_cache.clear()
        self._scan_and_store_missing_art()
        self._load_art_from_db()
        self._build_indexes()

    # ------------------------------------------------------------------
    # DB setup
    # ------------------------------------------------------------------

    def _open_db(self):
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self._create_schema()

    def _create_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            -- One row per physical audio file
            CREATE TABLE IF NOT EXISTS tracks (
                track_id    TEXT PRIMARY KEY,
                path        TEXT NOT NULL UNIQUE,
                title       TEXT NOT NULL,
                artist      TEXT NOT NULL,
                genre       TEXT,
                date        TEXT,
                duration    INTEGER DEFAULT 0,
                mtime       REAL    DEFAULT 0
            );

            -- One row per album (identified by normalised album_name + dir)
            -- album_name_norm is lowercase+stripped for case-insensitive dedup
            CREATE TABLE IF NOT EXISTS albums (
                album_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                album_name      TEXT NOT NULL,
                album_name_norm TEXT NOT NULL,
                album_artist    TEXT NOT NULL,
                album_dir       TEXT NOT NULL,
                UNIQUE (album_name_norm, album_dir)
            );

            -- Junction: tracks <-> albums with per-album track number and album tag
            CREATE TABLE IF NOT EXISTS album_tracks (
                album_id     INTEGER NOT NULL REFERENCES albums(album_id) ON DELETE CASCADE,
                track_id     TEXT    NOT NULL REFERENCES tracks(track_id)  ON DELETE CASCADE,
                track_number TEXT    DEFAULT '0',
                album_tag    TEXT    DEFAULT '',
                PRIMARY KEY (album_id, track_id)
            );

            -- Cover art BLOBs keyed by album_id
            CREATE TABLE IF NOT EXISTS album_art (
                album_id INTEGER PRIMARY KEY REFERENCES albums(album_id) ON DELETE CASCADE,
                blob     BLOB    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
            CREATE INDEX IF NOT EXISTS idx_tracks_title  ON tracks(title);
            CREATE INDEX IF NOT EXISTS idx_albums_name   ON albums(album_name);
            CREATE INDEX IF NOT EXISTS idx_at_track      ON album_tracks(track_id);
            CREATE INDEX IF NOT EXISTS idx_at_album      ON album_tracks(album_id);
        """)
        self._conn.commit()

    def _migrate_schema(self):
        """Migrate old flat schema (v1-v4) to normalised v5."""
        version = int(self._db_get_meta("db_version") or "0")
        if version >= DB_VERSION:
            return

        print(f"[Library] Migrating schema v{version} -> v{DB_VERSION} "
              f"(normalised tables)")

        # Check if old flat 'tracks' table exists with old columns
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(tracks)")}
        if "album" in cols:
            # Old schema — read all tracks out before we drop the table
            old_tracks = [dict(r) for r in self._conn.execute(
                "SELECT * FROM tracks")]
            print(f"[Library] Migrating {len(old_tracks)} tracks from old schema")

            # Drop old tables (cascades don't exist yet)
            self._conn.executescript("""
                DROP TABLE IF EXISTS album_tracks;
                DROP TABLE IF EXISTS album_art;
                DROP TABLE IF EXISTS albums;
                DROP TABLE IF EXISTS tracks;
            """)
            self._conn.commit()

            # Create new schema
            self._create_schema()

            # Re-insert tracks using new schema
            for t in old_tracks:
                path = t.get("path", "")
                if not path:
                    continue
                tid = t.get("id") or _track_id(path)
                tags = {
                    "title":       t.get("title", Path(path).stem),
                    "artist":      t.get("artist", "Unknown Artist"),
                    "album":       t.get("album", "Unknown Album"),
                    "tracknumber": t.get("tracknumber", "0"),
                    "genre":       t.get("genre", ""),
                    "date":        t.get("date", ""),
                    "duration":    t.get("duration", 0),
                }
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = t.get("mtime", 0.0)
                self._insert_track(tid, path, tags, mtime)

            self._conn.commit()
            print(f"[Library] Migration complete")

        self._db_set_meta("db_version", str(DB_VERSION))
        self._conn.commit()

    def _db_get_meta(self, key):
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _db_set_meta(self, key, value):
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        self._conn.commit()

    def _db_has_tracks(self):
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM tracks").fetchone()
        return row["n"] > 0

    # ------------------------------------------------------------------
    # Track insertion — normalised
    # ------------------------------------------------------------------

    def _insert_track(self, tid: str, path: str, tags: dict, mtime: float):
        """
        Insert or update one track in the normalised schema:
          1. Upsert into `tracks` (physical file — no album info here)
          2. Upsert into `albums` (keyed by album_name + album_dir)
          3. Upsert into `album_tracks` (junction with track_number + album_tag)
        """
        album_dir  = str(Path(path).parent)
        album_name = tags["album"].strip() or "Unknown Album"
        artist     = tags["artist"].strip() or "Unknown Artist"
        tracknumber= tags["tracknumber"]

        # 1. Upsert physical track (no album tag stored here)
        self._conn.execute("""
            INSERT INTO tracks (track_id, path, title, artist,
                                genre, date, duration, mtime)
            VALUES (:tid, :path, :title, :artist,
                    :genre, :date, :duration, :mtime)
            ON CONFLICT(path) DO UPDATE SET
                title    = excluded.title,
                artist   = excluded.artist,
                genre    = excluded.genre,
                date     = excluded.date,
                duration = excluded.duration,
                mtime    = excluded.mtime
            WHERE excluded.mtime != tracks.mtime
        """, {
            "tid": tid, "path": path,
            "title": tags["title"], "artist": artist,
            "genre": tags["genre"], "date": tags["date"],
            "duration": tags["duration"], "mtime": mtime,
        })

        # 2. Upsert album row — unique on (album_name_norm, album_dir)
        #    Normalised name is lowercase+stripped to handle tag variations
        #    like "(What's The Story)" vs "(What's the Story)" in same dir.
        album_name_norm = album_name.strip().lower()
        self._conn.execute("""
            INSERT INTO albums (album_name, album_name_norm, album_artist, album_dir)
            VALUES (:name, :norm, :artist, :dir)
            ON CONFLICT(album_name_norm, album_dir) DO NOTHING
        """, {"name": album_name, "norm": album_name_norm,
              "artist": artist, "dir": album_dir})

        # 3. Upsert album_tracks junction
        album_id = self._conn.execute("""
            SELECT album_id FROM albums
            WHERE album_name_norm=? AND album_dir=?
        """, (album_name_norm, album_dir)).fetchone()["album_id"]

        self._conn.execute("""
            INSERT INTO album_tracks (album_id, track_id, track_number, album_tag)
            VALUES (:aid, :tid, :tno, :atag)
            ON CONFLICT(album_id, track_id) DO UPDATE SET
                track_number = excluded.track_number,
                album_tag    = excluded.album_tag
        """, {
            "aid": album_id, "tid": tid,
            "tno": tracknumber, "atag": album_name,
        })

        # Update in-memory _tracks immediately
        self._tracks[tid] = {
            "id":          tid,
            "path":        path,
            "title":       tags["title"],
            "artist":      artist,
            "album":       album_name,
            "tracknumber": tracknumber,
            "genre":       tags["genre"],
            "date":        tags["date"],
            "duration":    tags["duration"],
        }

    # ------------------------------------------------------------------
    # Art helpers
    # ------------------------------------------------------------------

    def _load_art_from_db(self):
        self._art_cache.clear()
        for row in self._conn.execute(
                "SELECT album_id, blob FROM album_art"):
            self._art_cache[row["album_id"]] = bytes(row["blob"])
        print(f"[Library] Loaded {len(self._art_cache)} art BLOBs")

    def _scan_and_store_missing_art(self):
        """Store cover art for albums that don't have any yet."""
        # Albums with no art
        needs_art = {
            row["album_id"]: (row["album_dir"],)
            for row in self._conn.execute("""
                SELECT a.album_id, a.album_dir
                FROM albums a
                LEFT JOIN album_art aa ON aa.album_id = a.album_id
                WHERE aa.album_id IS NULL
            """)
        }

        # Collect first-track paths per album for embedded art fallback
        album_paths: dict[int, list] = {}
        for row in self._conn.execute("""
            SELECT at.album_id, t.path
            FROM album_tracks at
            JOIN tracks t ON t.track_id = at.track_id
            ORDER BY at.album_id, at.track_number
        """):
            album_paths.setdefault(row["album_id"], []).append(row["path"])

        saved = 0
        for album_id, (album_dir,) in needs_art.items():
            blob = None
            cover = _find_cover_file(album_dir)
            if cover:
                blob = _load_art_blob_from_file(cover)
            if not blob:
                for p in album_paths.get(album_id, [])[:3]:
                    blob = _load_art_blob_from_tags(p)
                    if blob:
                        break
            if blob:
                self._conn.execute(
                    "INSERT OR REPLACE INTO album_art(album_id,blob) VALUES(?,?)",
                    (album_id, sqlite3.Binary(blob)))
                saved += 1

        if saved:
            self._conn.commit()
            print(f"[Library] Stored {saved} new art BLOBs")

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _full_scan(self, progress_cb=None):
        self._conn.executescript("""
            DELETE FROM album_art;
            DELETE FROM album_tracks;
            DELETE FROM albums;
            DELETE FROM tracks;
        """)
        self._conn.commit()
        self._tracks.clear()
        self._art_cache.clear()

        files = self._find_files()
        for i, path in enumerate(files):
            if progress_cb:
                progress_cb(i + 1, len(files))
            tags  = _get_mutagen_tags(path)
            tid   = _track_id(path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            self._insert_track(tid, path, tags, mtime)

        self._conn.commit()
        self._scan_and_store_missing_art()
        self._db_set_meta("music_dir",  self.music_dir)
        self._db_set_meta("db_version", str(DB_VERSION))

    def _incremental_sync(self, progress_cb=None):
        on_disk: dict[str, float] = {}
        for path in self._find_files():
            try:
                on_disk[path] = os.path.getmtime(path)
            except OSError:
                pass

        in_db: dict[str, float] = {
            row["path"]: row["mtime"]
            for row in self._conn.execute("SELECT path, mtime FROM tracks")
        }

        to_remove = [p for p in in_db if p not in on_disk]
        to_add    = [p for p in on_disk if p not in in_db]
        to_update = [p for p in on_disk
                     if p in in_db and on_disk[p] != in_db[p]]

        if to_remove:
            for path in to_remove:
                tid = _track_id(path)
                self._conn.execute(
                    "DELETE FROM tracks WHERE track_id=?", (tid,))
                self._tracks.pop(tid, None)

        for i, path in enumerate(to_add + to_update):
            if progress_cb:
                progress_cb(i + 1, len(to_add) + len(to_update))
            tags  = _get_mutagen_tags(path)
            tid   = _track_id(path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            self._insert_track(tid, path, tags, mtime)

        if to_add or to_remove or to_update:
            self._conn.commit()
            self._scan_and_store_missing_art()

    def _load_from_db(self):
        """Load tracks from DB into _tracks cache."""
        self._tracks.clear()
        for row in self._conn.execute("""
            SELECT t.track_id, t.path, t.title, t.artist,
                   t.genre, t.date, t.duration,
                   at.album_tag   AS album,
                   at.track_number AS tracknumber
            FROM tracks t
            JOIN album_tracks at ON at.track_id = t.track_id
        """):
            d = dict(row)
            d["id"] = d.pop("track_id")
            # If same file on multiple albums, keep the last entry for now
            # (display handled in _build_indexes via album-scoped track dicts)
            self._tracks[d["id"]] = d

    def _find_files(self):
        files = []
        for root, _, filenames in os.walk(self.music_dir):
            for fn in filenames:
                if Path(fn).suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(os.path.join(root, fn))
        return files

    # ------------------------------------------------------------------
    # Index build — constructs in-memory _albums and _artists
    # ------------------------------------------------------------------

    def _build_indexes(self):
        """
        Build in-memory _albums and _artists from the normalised DB.

        Query: for each album, get all its tracks with their track numbers.
        Determine the display_artist via majority-vote on track artists.
        Update albums.album_artist in DB to match.
        """
        self._artists.clear()
        self._albums.clear()

        # Load all albums with their tracks in one query
        rows = self._conn.execute("""
            SELECT
                a.album_id,
                a.album_name,
                a.album_dir,
                t.track_id,
                t.path,
                t.title,
                t.artist,
                t.genre,
                t.date,
                t.duration,
                at.track_number,
                at.album_tag
            FROM albums a
            JOIN album_tracks at ON at.album_id = a.album_id
            JOIN tracks t        ON t.track_id  = at.track_id
            ORDER BY a.album_id, at.track_number
        """).fetchall()

        # Group by album_id
        album_rows: dict[int, list] = {}
        for row in rows:
            album_rows.setdefault(row["album_id"], []).append(row)

        for album_id, track_rows in album_rows.items():
            album_dir  = track_rows[0]["album_dir"]
            album_name = track_rows[0]["album_name"]

            # Majority-vote display artist
            counts = Counter(r["artist"] for r in track_rows)
            most_common, most_count = counts.most_common(1)[0]
            if most_count / len(track_rows) >= 0.5:
                display_artist = most_common
            else:
                display_artist = "Various Artists"

            # Build track dicts for this album — each track is unique by track_id
            seen_ids: set = set()
            tracks_for_album = []
            for r in track_rows:
                tid = r["track_id"]
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                tracks_for_album.append({
                    "id":          tid,
                    "path":        r["path"],
                    "title":       r["title"],
                    "artist":      r["artist"],
                    "album":       r["album_tag"] or album_name,
                    "tracknumber": r["track_number"] or "0",
                    "genre":       r["genre"] or "",
                    "date":        r["date"] or "",
                    "duration":    r["duration"] or 0,
                })

                # Also update global _tracks cache (last album wins for artist/album)
                self._tracks[tid] = tracks_for_album[-1]

            # Art blob from cache
            blob = self._art_cache.get(album_id)

            # Album key: unique per physical directory
            alb_key = f"{display_artist}::{album_name}::{album_dir}"

            alb_dict = {
                "name":     album_name,
                "artist":   display_artist,
                "key":      alb_key,
                "art":      blob,
                "tracks":   tracks_for_album,
                "dir":      album_dir,
                "album_id": album_id,
            }

            self._albums[alb_key] = alb_dict

            # Artists index
            if display_artist not in self._artists:
                self._artists[display_artist] = {
                    "name": display_artist, "albums": {}}
            self._artists[display_artist]["albums"][alb_key] = alb_dict

        print(f"[Library] Index: {len(self._albums)} albums, "
              f"{len(self._tracks)} tracks, {len(self._artists)} artists")

    # ------------------------------------------------------------------

    @staticmethod
    def _track_num(tn):
        try:
            return int(str(tn).split("/")[0])
        except (ValueError, TypeError):
            return 0
