"""
PulseBox Web Radio Page
Streams internet radio via VLC.
Uses queue-based thread dispatch — no Tkinter calls from background threads.
"""

import io
import json
import os
import hashlib
import queue
import threading
import tkinter as tk
from tkinter import ttk
import urllib.parse

from .theme import *
from .widgets import ScrollableFrame

RADIO_API  = "https://de1.api.radio-browser.info/json"
CACHE_DIR  = os.path.expanduser("~/.pulsebox_radio_cache")
# Station icon scales with screen resolution (like the album grid's CARD_W),
# so the station cards grow on larger displays and the row fills.
ICON_SIZE  = max(40, int(64 * get_scale()))

# BBC streams use Akamai HLS (ww = worldwide, uk = UK-only for sports/restricted)
# Source: https://gist.github.com/bpsib/67089b959e4fa898af69fea59ad74bc3
_BBC = "http://as-hls-ww-live.akamaized.net"
_UK  = "http://as-hls-uk-live.akamaized.net"

def _bbc(pool, station, bitrate=96000, uk=False):
    base = _UK if uk else _BBC
    return (f"{base}/{pool}/live/{'uk' if uk else 'ww'}/{station}"
            f"/{station}.isml/{station}-audio%3d{bitrate}.norewind.m3u8")

FALLBACK_STREAMS = {
    # ── BBC National ──────────────────────────────────────────────────────────
    "BBC Radio 1":          (_bbc("pool_01505109", "bbc_radio_one"),              "Pop"),
    "BBC Radio 2":          (_bbc("pool_74208725", "bbc_radio_two"),              "Pop"),
    "BBC Radio 3":          (_bbc("pool_23461179", "bbc_radio_three"),            "Classical"),
    "BBC Radio 4":          (_bbc("pool_55057080", "bbc_radio_fourfm"),           "Talk"),
    "BBC Radio 4 Extra":    (_bbc("pool_26173715", "bbc_radio_four_extra"),       "Talk"),
    "BBC Radio 5 Live":     (_bbc("pool_89021708", "bbc_radio_five_live"),        "Talk"),
    "BBC Radio 6 Music":    (_bbc("pool_81827798", "bbc_6music"),                 "Alternative"),
    "BBC World Service":    (_bbc("pool_87948813", "bbc_world_service"),          "Talk"),
    # ── BBC Nations ───────────────────────────────────────────────────────────
    "BBC Radio Scotland":   (_bbc("pool_43322914", "bbc_radio_scotland_fm"),      "Talk"),
    "BBC Radio Wales":      (_bbc("pool_97517794", "bbc_radio_wales_fm"),         "Talk"),
    "BBC Radio Ulster":     (_bbc("pool_31244774", "bbc_radio_ulster"),           "Talk"),
    "BBC Radio nan Gaidheal": (_bbc("pool_01935182", "bbc_radio_nan_gaidheal"),   "Talk"),
    # ── Commercial ───────────────────────────────────────────────────────────
    "Classic FM":           ("http://media-ice.musicradio.com/ClassicFMMP3",      "Classical"),
    "Absolute Radio":       ("http://icy.absoluteradio.co.uk/absoluteradio.mp3",  "Rock"),
    "Jazz FM":              ("http://edge-bauermedia.sharp-stream.com/jazzfmuk.mp3", "Jazz"),
    "Heart FM":             ("http://media-ice.musicradio.com/HeartMP3",          "Pop"),
    "Capital FM":           ("http://media-ice.musicradio.com/CapitalMP3",        "Pop"),
}

# Known-good favicon URLs for popular stations (used when API returns empty favicon)
_BBC_ART = "https://sounds.files.bbci.co.uk/2.3.0/networks"
_BBC_SFX = "blocks-programmes-highlights_colour/600x600.jpg"

KNOWN_FAVICONS = {
    # BBC national
    "bbc radio 1":            f"{_BBC_ART}/bbc_radio_one/{_BBC_SFX}",
    "bbc radio 2":            f"{_BBC_ART}/bbc_radio_two/{_BBC_SFX}",
    "bbc radio 3":            f"{_BBC_ART}/bbc_radio_three/{_BBC_SFX}",
    "bbc radio 4 extra":      f"{_BBC_ART}/bbc_radio_four_extra/{_BBC_SFX}",
    "bbc radio 4":            f"{_BBC_ART}/bbc_radio_four/{_BBC_SFX}",
    "bbc radio 5 live":       f"{_BBC_ART}/bbc_radio_five_live/{_BBC_SFX}",
    "bbc radio 5":            f"{_BBC_ART}/bbc_radio_five_live/{_BBC_SFX}",
    "bbc radio 6":            f"{_BBC_ART}/bbc_6music/{_BBC_SFX}",
    "bbc 6 music":            f"{_BBC_ART}/bbc_6music/{_BBC_SFX}",
    "bbc world service":      f"{_BBC_ART}/bbc_world_service/{_BBC_SFX}",
    # BBC nations
    "bbc radio scotland":     f"{_BBC_ART}/bbc_radio_scotland/{_BBC_SFX}",
    "bbc radio wales":        f"{_BBC_ART}/bbc_radio_wales/{_BBC_SFX}",
    "bbc radio ulster":       f"{_BBC_ART}/bbc_radio_ulster/{_BBC_SFX}",
    "bbc radio nan gaidheal": f"{_BBC_ART}/bbc_radio_nan_gaidheal/{_BBC_SFX}",
    # Other
    "somafm":            "https://somafm.com/img3/groovesalad-400.jpg",
    "soma fm":           "https://somafm.com/img3/groovesalad-400.jpg",
    "groove salad":      "https://somafm.com/img3/groovesalad-400.jpg",
    "drone zone":        "https://somafm.com/img3/dronezone-400.jpg",
    "radio paradise":    "https://radioparadise.com/graphics/logos/rp_logo_600.png",
    "kexp":              "https://www.kexp.org/static/img/kexp-og-image.jpg",
    "fip":               "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/FIP_logo_2021.svg/600px-FIP_logo_2021.svg.png",
}

def _best_favicon(name: str, api_favicon: str) -> str:
    """Return best favicon URL: API value if valid, else known fallback."""
    if api_favicon and api_favicon.startswith("http"):
        return api_favicon
    name_lower = name.lower()
    for key, url in KNOWN_FAVICONS.items():
        if key in name_lower:
            return url
    return ""

GENRE_COLOURS = {
    "pop": "#e8b004", "rock": "#ff6b35", "classical": "#8866ff",
    "jazz": "#00cc88", "talk": "#00d4ff", "electronic": "#ff2d78",
    "alternative": "#ff6b35", "ambient": "#4488ff", "indie": "#cc44ff",
}

def _genre_colour(genre: str) -> str:
    g = genre.lower()
    for k, c in GENRE_COLOURS.items():
        if k in g:
            return c
    return ACCENT


def _fetch_stations(query: str = "", countrycode: str = "",
                    limit: int = 36) -> list[dict]:
    """
    Fetch stations from Radio Browser.
    Always builds favicon URL from stationuuid via Radio Browser CDN —
    the station favicon field is often empty but the CDN works by UUID.
    """
    try:
        import requests
        params = {"limit": limit, "hidebroken": "true",
                  "order": "votes", "reverse": "true"}
        if query:
            params["name"] = query
        if countrycode:
            params["countrycode"] = countrycode
        r = requests.get(
            f"{RADIO_API}/stations/search?" + urllib.parse.urlencode(params),
            headers={"User-Agent": "PulseBox/5.5"},
            timeout=10)
        r.raise_for_status()
        result = []
        for s in r.json():
            url = s.get("url_resolved") or s.get("url", "")
            if not url:
                continue
            uuid = s.get("stationuuid", "")
            # Use favicon URL directly from API — matches working test script
            favicon = s.get("favicon", "").strip()
            result.append({
                "name":    s.get("name", "").strip(),
                "url":     url,
                "country": s.get("countrycode", ""),
                "genre":   (s.get("tags", "").split(",")[0].strip().title()
                            if s.get("tags") else "Radio"),
                "favicon": favicon,
            })
        return result
    except Exception as e:
        print(f"[Radio] fetch error: {e}")
        return []


def _download_logo(favicon_url: str, size: int):
    """
    Download and process a station logo.
    Returns ImageTk.PhotoImage or None.
    Uses Radio Browser favicon CDN which returns placeholder for missing logos.
    Filters out tiny placeholder images (< 1KB).
    """
    if not favicon_url or not favicon_url.startswith("http"):
        return None
    try:
        import requests
        from PIL import Image, ImageTk

        os.makedirs(CACHE_DIR, exist_ok=True)
        key   = hashlib.md5(favicon_url.encode()).hexdigest()[:16]
        cpath = os.path.join(CACHE_DIR, key + ".png")

        if os.path.exists(cpath):
            # Skip tiny placeholder cache files
            if os.path.getsize(cpath) < 500:
                return None
            img = Image.open(cpath).convert("RGBA")
        else:
            resp = requests.get(favicon_url,
                                headers={"User-Agent": "PulseBox/5.5"},
                                timeout=6)
            resp.raise_for_status()
            data = resp.content

            # Skip tiny responses — placeholder/missing logo
            if len(data) < 1000:
                print(f"[Radio] logo too small ({len(data)}b), skipping: {favicon_url[:50]}")
                return None

            img = Image.open(io.BytesIO(data)).convert("RGBA")
            w, h = img.size

            # Skip tiny images (1x1 pixel placeholders etc)
            if w < 16 or h < 16:
                return None

            # Square-crop then resize to exact size so all icons match
            m    = min(w, h)
            left = (w - m) // 2
            top  = (h - m) // 2
            img  = img.crop((left, top, left + m, top + m))
            img  = img.resize((size, size), Image.Resampling.LANCZOS)
            img.save(cpath, "PNG")

        img = img.resize((size, size), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)

    except Exception as e:
        print(f"[Radio] logo error {favicon_url[:60]}: {e}")
        return None


# ---------------------------------------------------------------------------
# Country list + flag imagery (top level of the radio browser)
# ---------------------------------------------------------------------------
FLAG_W   = max(48, int(72 * get_scale()))
FLAG_H   = max(36, int(54 * get_scale()))
FLAG_CDN = "https://flagcdn.com/w160/{cc}.png"   # free flag PNGs by ISO code


def _flag_url(cc: str) -> str:
    return FLAG_CDN.format(cc=cc.lower())


def _fetch_countries() -> list[dict]:
    """
    Fetch the list of countries (with station counts) from Radio Browser.
    Returns [{"code": "GB", "name": "United Kingdom", "count": 1234}, ...]
    sorted alphabetically by name. Entries without a valid 2-letter ISO code
    or with no stations are dropped.
    """
    try:
        import requests
        r = requests.get(f"{RADIO_API}/countries",
                         headers={"User-Agent": "PulseBox/5.5"}, timeout=10)
        r.raise_for_status()
        out = []
        # Radio Browser sometimes returns non-ISO placeholder codes (e.g. "XX"
        # for unknown), which have no flag — skip them.
        NON_ISO = {"XX", "XY", "ZZ"}
        for c in r.json():
            code = (c.get("iso_3166_1") or "").strip().upper()
            name = (c.get("name") or "").strip()
            cnt  = int(c.get("stationcount", 0) or 0)
            if (len(code) == 2 and code.isalpha()
                    and code not in NON_ISO and cnt > 0):
                out.append({"code": code, "name": name or code, "count": cnt})
        out.sort(key=lambda x: x["name"].lower())
        return out
    except Exception as e:
        print(f"[Radio] countries fetch error: {e}")
        return []


def _download_flag(cc: str, box_w: int, box_h: int):
    """
    Download a country flag PNG and scale it to fit box_w x box_h while
    preserving aspect ratio. Cached on disk. Returns PhotoImage or None.
    """
    if not cc or len(cc) != 2:
        return None
    url = _flag_url(cc)
    try:
        import requests
        from PIL import Image, ImageTk
        os.makedirs(CACHE_DIR, exist_ok=True)
        cpath = os.path.join(CACHE_DIR, f"flag_{cc.lower()}.png")
        if os.path.exists(cpath) and os.path.getsize(cpath) > 200:
            img = Image.open(cpath).convert("RGBA")
        else:
            resp = requests.get(url, headers={"User-Agent": "PulseBox/5.5"},
                                timeout=6)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            img.save(cpath, "PNG")
        w, h = img.size
        scale = min(box_w / w, box_h / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception as e:
        # A missing flag (404) is expected for some codes — don't make noise;
        # the card just keeps its placeholder.
        if "404" not in str(e):
            print(f"[Radio] flag error {cc}: {e}")
        return None


# ---------------------------------------------------------------------------
# Podcasts (Apple/iTunes — free, no API key)
# ---------------------------------------------------------------------------
# Top podcasts per country come from the public iTunes RSS feed. To actually
# play one we look up its RSS feed URL and grab the newest episode's audio.
ITUNES_TOP  = "https://itunes.apple.com/{cc}/rss/toppodcasts/limit={n}/json"
ITUNES_LOOK = "https://itunes.apple.com/lookup?id={pid}"


def _fetch_podcasts(cc: str, limit: int = 30) -> list[dict]:
    """
    Fetch the top podcasts for a country from the iTunes RSS feed.
    Returns [{"name", "image", "podcast_id", "genre"}, ...].
    """
    try:
        import requests
        url = ITUNES_TOP.format(cc=(cc or "gb").lower(), n=limit)
        r = requests.get(url, headers={"User-Agent": "PulseBox/5.5"}, timeout=10)
        r.raise_for_status()
        entries = r.json().get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        out = []
        for e in entries:
            name = e.get("im:name", {}).get("label", "").strip()
            pid  = e.get("id", {}).get("attributes", {}).get("im:id", "")
            imgs = e.get("im:image", [])
            image = imgs[-1].get("label", "") if imgs else ""
            genre = e.get("category", {}).get("attributes", {}).get("label", "Podcast")
            if name and pid:
                out.append({"name": name, "image": image,
                            "podcast_id": pid, "genre": genre})
        return out
    except Exception as e:
        print(f"[Radio] podcasts fetch error ({cc}): {e}")
        return []


def _resolve_podcast_audio(podcast_id: str) -> str:
    """
    Resolve a podcast's newest episode audio URL:
      iTunes lookup -> feedUrl -> parse RSS -> first <enclosure url>.
    Returns the audio URL or "".
    """
    try:
        import requests
        import xml.etree.ElementTree as ET
        r = requests.get(ITUNES_LOOK.format(pid=podcast_id),
                         headers={"User-Agent": "PulseBox/5.5"}, timeout=8)
        r.raise_for_status()
        results = r.json().get("results", [])
        feed_url = results[0].get("feedUrl") if results else ""
        if not feed_url:
            return ""
        fr = requests.get(feed_url, headers={"User-Agent": "PulseBox/5.5"},
                          timeout=12)
        fr.raise_for_status()
        root = ET.fromstring(fr.content)
        for item in root.iter("item"):
            enc = item.find("enclosure")
            if enc is not None and enc.get("url"):
                return enc.get("url")
        return ""
    except Exception as e:
        print(f"[Radio] podcast resolve error ({podcast_id}): {e}")
        return ""


class RadioPage(tk.Frame):

    def __init__(self, parent, player, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._player          = player
        self._stations: list  = []
        self._current_station = None
        self._photos: dict      = {}   # favicon_url -> PhotoImage|None
        self._photo_refs: list  = []   # prevent GC of PhotoImages
        self._canvases: list    = []   # (canvas, favicon_url) pairs for current grid
        self._ui_queue          = queue.Queue()
        self._searching       = False

        # Country-flag browsing level
        self._countries: list   = []
        self._current_country   = None   # (code, name) or None
        self._view              = "countries"   # countries | stations | podcasts
        self._flag_photos: dict = {}     # flag_url -> PhotoImage|None
        self._flag_canvases: list = []   # (canvas, code) for current country grid

        # Podcasts
        self._podcasts: list    = []

        # Responsive grid layout — cards are laid out to fill the viewport and
        # re-flow when the width changes (fixes first-render column count)
        self._grid_cards: list  = []
        self._grid_card_w       = 0
        self._last_cols         = -1
        self._reflow_after      = None

        self._build()
        # Poll queue every 50ms to process results from background threads
        self._poll()

    def _dispatch(self, fn):
        """Queue a function to run on the main thread."""
        self._ui_queue.put(fn)

    def _poll(self):
        """Drain the UI queue — runs on main thread via after()."""
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                fn()
        except queue.Empty:
            pass
        self.after(50, self._poll)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=BG_DARK)
        hdr.pack(fill="x", padx=PAD, pady=(PAD, 4))
        tk.Label(hdr, text="INTERNET RADIO", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(side="left")
        self._status_lbl = tk.Label(hdr, text="",
                                    font=FONT_SMALL, fg=NEON_BLUE,
                                    bg=BG_DARK)
        self._status_lbl.pack(side="right")

        sr = tk.Frame(self, bg=BG_DARK)
        sr.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        self._search_var = tk.StringVar()  # kept for _on_search compatibility
        # Back-to-countries button (only shown while viewing a country's stations)
        self._back_btn = tk.Button(sr, text="◀ Countries",
                                   font=FONT_SMALL, fg=NEON_BLUE, bg=BG_CARD,
                                   relief="flat", bd=0, cursor="hand2",
                                   padx=10, pady=4,
                                   command=self._back_to_countries)
        tk.Button(sr, text="⏹ Stop",
                  font=FONT_SMALL, fg=DANGER, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=10, pady=4,
                  command=self._stop).pack(side="left")

        # Quick filters
        qr = tk.Frame(self, bg=BG_DARK)
        qr.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        tk.Label(qr, text="Browse:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack(side="left")
        # "podcasts" is a special action (cc="", q="__podcasts__"); the rest
        # are genre searches across all countries.
        for label, cc, q in [
            ("🎙 Podcasts",   "",   "__podcasts__"),
            ("🎷 Jazz",        "",   "jazz"),
            ("🎻 Classical",   "",   "classical"),
            ("🎸 Rock",        "",   "rock"),
            ("🎛 Electronic",  "",   "electronic"),
            ("🌊 Ambient",     "",   "ambient"),
        ]:
            tk.Button(qr, text=label, font=FONT_TINY,
                      fg=BG_DARK, bg=_genre_colour(label),
                      relief="flat", bd=0, cursor="hand2",
                      padx=8, pady=3,
                      command=lambda c=cc, qr=q: self._quick(c, qr)
                      ).pack(side="left", padx=3)

        # Grid
        self._scroll = ScrollableFrame(self, bg=BG_DARK)
        self._scroll.pack(fill="both", expand=True)
        self._grid  = self._scroll.inner
        # Re-flow the current grid when the viewport width becomes known or
        # changes (this is what makes the first country render fill the screen)
        self._scroll._canvas.bind("<Configure>", self._on_viewport_resize,
                                  add="+")

        # Loading label
        self._loading_lbl = tk.Label(
            self._grid, text="Loading countries…",
            font=FONT_HEADING, fg=TEXT_PRIMARY, bg=BG_DARK)
        self._loading_lbl.grid(row=0, column=0, pady=PAD*4)

        # Top level: show the country flag grid once the widget is mapped
        self.after(200, self._load_countries)

    # ------------------------------------------------------------------
    # Station loading
    # ------------------------------------------------------------------

    def _load(self, countrycode: str = "GB", query: str = ""):
        """Start background fetch."""
        self._set_status("Loading…")
        threading.Thread(
            target=self._bg_fetch,
            args=(countrycode, query),
            daemon=True).start()

    def _bg_fetch(self, countrycode: str, query: str):
        stations = _fetch_stations(query=query, countrycode=countrycode)

        # Prepend the guaranteed BBC/known UK streams only when browsing the
        # UK with no search term. For other countries we show exactly what the
        # Radio Browser API returns.
        is_uk = (countrycode == "GB")
        if not query and is_uk:
            api_names_lower = {s["name"].lower() for s in stations}
            prepend = []
            for name, (url, genre) in FALLBACK_STREAMS.items():
                if name.lower() not in api_names_lower:
                    prepend.append({
                        "name":    name,
                        "url":     url,
                        "country": "GB",
                        "genre":   genre,
                        "favicon": _best_favicon(name, ""),
                    })
            stations = prepend + stations

        if not stations and is_uk:
            # Absolute fallback — API down and browsing the UK
            stations = [
                {"name": n, "url": u, "country": "GB",
                 "genre": g, "favicon": _best_favicon(n, "")}
                for n, (u, g) in FALLBACK_STREAMS.items()
            ]

        self._dispatch(lambda s=stations: self._show_stations(s))

    def _show_stations(self, stations: list):
        print(f"[Radio] _show_stations: {len(stations)} stations")
        self._stations = stations
        self._render_grid()

    # ------------------------------------------------------------------
    # Country level (top of the browser)
    # ------------------------------------------------------------------

    def _quick(self, cc: str, q: str):
        """Handle a Browse quick-filter button."""
        if q == "__podcasts__":
            # Top podcasts for the country you're in, or the UK by default
            code = self._current_country[0] if self._current_country else "GB"
            name = self._current_country[1] if self._current_country else "UK"
            self._load_podcasts(code, name)
            return
        if cc:
            name = next((x["name"] for x in self._countries if x["code"] == cc),
                        cc)
            self._open_country(cc, name)
        else:
            self._current_country = None
            self._enter_stations_view()
            self._set_status(f"{q.title()} …")
            self._load("", q)

    def _enter_stations_view(self):
        self._view = "stations"
        try:
            self._back_btn.pack(side="left", padx=(0, 8))
        except Exception:
            pass

    def _back_to_countries(self):
        self._view = "countries"
        self._current_country = None
        try:
            self._back_btn.pack_forget()
        except Exception:
            pass
        if self._countries:
            self._render_countries()
        else:
            self._load_countries()

    def _load_countries(self):
        self._view = "countries"
        try:
            self._back_btn.pack_forget()
        except Exception:
            pass
        self._set_status("Loading…")
        threading.Thread(target=self._bg_fetch_countries, daemon=True).start()

    def _bg_fetch_countries(self):
        countries = _fetch_countries()
        self._dispatch(lambda c=countries: self._show_countries(c))

    def _show_countries(self, countries: list):
        self._countries = countries
        self._view = "countries"
        self._render_countries()

    def _render_countries(self):
        self._flag_canvases = []
        for w in self._grid.winfo_children():
            w.destroy()

        if not self._countries:
            tk.Label(self._grid,
                     text="No countries found — check the internet connection",
                     font=FONT_BODY, fg=TEXT_PRIMARY,
                     bg=BG_DARK).grid(row=0, column=0, pady=PAD*3)
            self._set_status("No results")
            return

        self._set_status(f"{len(self._countries)} countries")
        cards = [self._make_country_card(c, 0, 0) for c in self._countries]
        self._commit_grid(cards, FLAG_W)
        self._load_flags()

    def _commit_grid(self, cards: list, default_card_w: int):
        """Store the freshly-built cards and lay them out to fill the row.

        Card width is measured from a real card so the column count always
        matches the actual (scaled) card size. Re-flows automatically when the
        viewport width changes (see _on_viewport_resize / _layout_grid).
        """
        self._grid.update_idletasks()
        self._grid_cards  = cards
        self._grid_card_w = ((cards[0].winfo_reqwidth() if cards
                              else default_card_w) + 2 * PAD_SM)
        self._last_cols   = -1            # force a fresh layout
        self._layout_grid()
        self._scroll.bind_all_children_scroll(self._grid)
        self._scroll.refresh_scrollregion()

    def _layout_grid(self):
        """(Re)place the current cards using as many columns as fit."""
        cards = self._grid_cards
        if not cards:
            return
        try:
            if not cards[0].winfo_exists():
                return
        except Exception:
            return
        avail = self._scroll.viewport_width()
        if not avail:
            avail = self.winfo_width() - 24
        card_w = max(1, self._grid_card_w)
        if avail < card_w:
            avail = 1280
        cols = max(1, avail // card_w)
        if cols == self._last_cols:
            return
        self._last_cols = cols
        for i, card in enumerate(cards):
            r, c = divmod(i, cols)
            card.grid(row=r, column=c, padx=PAD_SM, pady=PAD_SM, sticky="nw")
        self._scroll.refresh_scrollregion()

    def _on_viewport_resize(self, _e=None):
        """Debounced re-flow when the viewport width changes."""
        if self._reflow_after:
            try:
                self.after_cancel(self._reflow_after)
            except Exception:
                pass
        self._reflow_after = self.after(120, self._layout_grid)

    def _make_country_card(self, country: dict, row: int, col: int) -> tk.Frame:
        code  = country["code"]
        name  = country["name"]
        count = country["count"]

        card = tk.Frame(self._grid, bg=BG_CARD, cursor="hand2", padx=6, pady=6)
        tk.Frame(card, bg=NEON_BLUE, height=3).pack(fill="x")

        fc = tk.Canvas(card, width=FLAG_W, height=FLAG_H,
                       bg=BG_CARD, highlightthickness=0)
        fc.pack(pady=(6, 4))
        self._draw_flag_placeholder(fc, code)
        self._flag_canvases.append((fc, code))

        disp = name[:15].rstrip()
        if len(name) > 15:
            disp = disp[:14].rstrip() + "…"
        tk.Label(card, text=disp, font=FONT_TRACK_SM, fg=TEXT_PRIMARY,
                 bg=BG_CARD, width=15, anchor="center",
                 justify="center").pack()
        tk.Label(card, text=f"{count} stn", font=FONT_TINY,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack()

        def _click(e=None, c=code, n=name):
            self._open_country(c, n)
        for w in [card] + list(card.winfo_children()):
            w.bind("<Button-1>", _click)
        return card

    def _draw_flag_placeholder(self, canvas: tk.Canvas, code: str):
        canvas.delete("all")
        canvas.create_rectangle(0, 0, FLAG_W, FLAG_H,
                                fill=BG_CARD_HOVER, outline="")
        canvas.create_text(FLAG_W // 2, FLAG_H // 2, text=code,
                           font=("Helvetica", int(FLAG_H * 0.38), "bold"),
                           fill=TEXT_PRIMARY)

    def _open_country(self, code: str, name: str):
        """Drill into a country: show its stations."""
        self._current_country = (code, name)
        self._enter_stations_view()
        self._set_status(f"{name} — loading…")
        for w in self._grid.winfo_children():
            w.destroy()
        tk.Label(self._grid, text=f"Loading {name} stations…",
                 font=FONT_HEADING, fg=TEXT_PRIMARY,
                 bg=BG_DARK).grid(row=0, column=0, pady=PAD*4)
        self._load(code, "")

    # ------------------------------------------------------------------
    # Flag image loading (bounded worker pool)
    # ------------------------------------------------------------------

    def _load_flags(self):
        work = queue.Queue()
        for canvas, code in self._flag_canvases:
            url = _flag_url(code)
            if url in self._flag_photos:
                photo = self._flag_photos[url]
                if photo:
                    self._apply_flag(canvas, photo)
                continue
            work.put((canvas, code))

        workers = min(6, max(1, work.qsize()))
        for _ in range(workers):
            threading.Thread(target=self._flag_worker,
                             args=(work,), daemon=True).start()

    def _flag_worker(self, work: queue.Queue):
        while True:
            try:
                canvas, code = work.get_nowait()
            except queue.Empty:
                return
            url   = _flag_url(code)
            photo = _download_flag(code, FLAG_W, FLAG_H)
            self._flag_photos[url] = photo
            if photo:
                def _update(p=photo, cv=canvas):
                    self._apply_flag(cv, p)
                self._dispatch(_update)

    def _apply_flag(self, canvas: tk.Canvas, photo):
        if photo is None:
            return
        try:
            if not canvas.winfo_exists():
                return
            canvas.delete("all")
            canvas._photo = photo
            self._photo_refs.append(photo)
            canvas.create_image(FLAG_W // 2, FLAG_H // 2,
                                image=photo, anchor="center")
        except Exception as e:
            print(f"[Radio] apply flag error: {e}")

    # ------------------------------------------------------------------
    # Podcasts
    # ------------------------------------------------------------------

    def _load_podcasts(self, cc: str, country_name: str):
        self._view = "podcasts"
        try:
            self._back_btn.pack(side="left", padx=(0, 8))
        except Exception:
            pass
        self._set_status(f"Top podcasts — {country_name} …")
        for w in self._grid.winfo_children():
            w.destroy()
        tk.Label(self._grid, text=f"Loading {country_name} podcasts…",
                 font=FONT_HEADING, fg=TEXT_PRIMARY,
                 bg=BG_DARK).grid(row=0, column=0, pady=PAD*4)
        threading.Thread(target=self._bg_fetch_podcasts,
                         args=(cc, country_name), daemon=True).start()

    def _bg_fetch_podcasts(self, cc: str, country_name: str):
        podcasts = _fetch_podcasts(cc)
        self._dispatch(lambda p=podcasts, n=country_name:
                       self._show_podcasts(p, n))

    def _show_podcasts(self, podcasts: list, country_name: str):
        self._podcasts = podcasts
        self._view = "podcasts"
        self._render_podcasts(country_name)

    def _render_podcasts(self, country_name: str = ""):
        self._canvases = []
        for w in self._grid.winfo_children():
            w.destroy()
        if not self._podcasts:
            tk.Label(self._grid,
                     text="No podcasts found for this country",
                     font=FONT_BODY, fg=TEXT_PRIMARY,
                     bg=BG_DARK).grid(row=0, column=0, pady=PAD*3)
            self._set_status("No podcasts")
            return
        label = f"{len(self._podcasts)} podcasts"
        if country_name:
            label += f" — {country_name}"
        self._set_status(label)
        cards = [self._make_podcast_card(p) for p in self._podcasts]
        self._commit_grid(cards, ICON_SIZE)
        self._load_logos()

    def _make_podcast_card(self, podcast: dict) -> tk.Frame:
        playing = (self._current_station and
                   self._current_station.get("name") == podcast["name"])
        bg     = BG_CARD_HOVER if playing else BG_CARD
        colour = _genre_colour("podcast")

        card = tk.Frame(self._grid, bg=bg, cursor="hand2", padx=6, pady=6)
        tk.Frame(card, bg=colour, height=3).pack(fill="x")

        ic = tk.Canvas(card, width=ICON_SIZE, height=ICON_SIZE,
                       bg=bg, highlightthickness=0)
        ic.pack(pady=(6, 4))
        self._draw_placeholder(ic, podcast["name"], colour)
        image = podcast.get("image", "")
        if image:
            self._canvases.append((ic, image))

        name = podcast["name"][:13].rstrip()
        if len(podcast["name"]) > 13:
            name = name[:12].rstrip() + "…"
        tk.Label(card, text=name, font=FONT_TRACK_SM, fg=TEXT_PRIMARY,
                 bg=bg, width=13, anchor="center", justify="center").pack()

        if playing:
            tk.Frame(card, bg=NEON_BLUE, height=2).pack(fill="x")

        def _click(e=None, p=podcast):
            self._play_podcast(p)
        for w in [card] + list(card.winfo_children()):
            w.bind("<Button-1>", _click)
        return card

    def _play_podcast(self, podcast: dict):
        """Resolve the newest episode and play it (runs off the UI thread)."""
        self._set_status(f"Resolving {podcast['name']}…")

        def _bg(p=podcast):
            url = _resolve_podcast_audio(p["podcast_id"])
            if url:
                station = {"name": p["name"], "url": url, "country": "",
                           "genre": "Podcast", "favicon": p.get("image", "")}
                self._dispatch(lambda s=station: self._play(s))
            else:
                self._dispatch(lambda n=p["name"]:
                               self._set_status(f"Couldn't load {n}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _render_current(self):
        """Re-render whichever view is active (used after play/stop)."""
        if self._view == "podcasts":
            self._render_podcasts()
        elif self._view == "countries":
            self._render_countries()
        else:
            self._render_grid()

    # ------------------------------------------------------------------
    # Grid rendering
    # ------------------------------------------------------------------

    def _render_grid(self):
        self._canvases = []   # clear canvas refs for new grid
        for w in self._grid.winfo_children():
            w.destroy()

        stations = self._stations
        if not stations:
            tk.Label(self._grid, text="No stations found",
                     font=FONT_BODY, fg=TEXT_PRIMARY,
                     bg=BG_DARK).grid(row=0, column=0, pady=PAD*3)
            self._set_status("No results")
            return

        self._set_status(f"{len(stations)} stations")
        cards = [self._make_card(s, 0, 0) for s in stations]
        self._commit_grid(cards, ICON_SIZE)
        # Load logos immediately — self._canvases already populated above
        self._load_logos()

    def _make_card(self, station: dict, row: int, col: int) -> tk.Frame:
        playing = (self._current_station and
                   self._current_station.get("name") == station["name"])
        bg     = BG_CARD_HOVER if playing else BG_CARD
        colour = _genre_colour(station.get("genre", ""))

        card = tk.Frame(self._grid, bg=bg, cursor="hand2",
                        padx=6, pady=6)
        # DO NOT use pack_propagate(False) — let frame size to contents
        tk.Frame(card, bg=colour, height=3).pack(fill="x")

        # Icon canvas — fixed size, always visible
        ic = tk.Canvas(card, width=ICON_SIZE, height=ICON_SIZE,
                       bg=bg, highlightthickness=0)
        ic.pack(pady=(6,4))
        self._draw_placeholder(ic, station["name"], colour)
        # Store (canvas, favicon_url) for logo loading
        favicon = station.get("favicon", "")
        if favicon:
            self._canvases.append((ic, favicon))

        # Truncate to 16 chars then wrap — keeps all cards same width
        # Name — truncated to 13 chars, fixed width keeps all cards same size
        name = station["name"][:13].rstrip()
        if len(station["name"]) > 13:
            name = name[:12].rstrip() + "…"
        tk.Label(card, text=name,
                 font=FONT_TRACK_SM, fg=TEXT_PRIMARY, bg=bg,
                 width=13, anchor="center",
                 justify="center").pack()

        if playing:
            tk.Frame(card, bg=NEON_BLUE, height=2).pack(fill="x")

        def _click(e=None, s=station):
            self._play(s)
        for w in [card] + list(card.winfo_children()):
            w.bind("<Button-1>", _click)

        return card

    def _draw_placeholder(self, canvas, name: str, colour: str):
        """Default icon — coloured box with station name."""
        canvas.delete("all")
        s = ICON_SIZE
        # Solid colour background
        canvas.create_rectangle(0, 0, s, s, fill=colour, outline="")
        # Wrap name into box — up to 2 lines of 8 chars
        words = name.split()
        line1, line2 = "", ""
        for w in words:
            test = (line1 + " " + w).strip()
            if len(test) <= 8:
                line1 = test
            elif not line2:
                line2 = w[:8]
            else:
                break
        fs = max(7, int(s * 0.17))
        if line2:
            canvas.create_text(s//2, s//2 - fs,
                               text=line1,
                               font=("Helvetica", fs, "bold"),
                               fill=BG_DARK, width=s-2, justify="center")
            canvas.create_text(s//2, s//2 + fs,
                               text=line2,
                               font=("Helvetica", fs, "bold"),
                               fill=BG_DARK, width=s-2, justify="center")
        else:
            canvas.create_text(s//2, s//2,
                               text=line1 or name[:8],
                               font=("Helvetica", fs, "bold"),
                               fill=BG_DARK, width=s-2, justify="center")

    def _load_logos(self):
        """Load logos for all canvases registered during grid build."""
        print(f"[Radio] _load_logos: {len(self._canvases)} canvases queued")
        for canvas, favicon_url in self._canvases:
            self._load_one_logo(canvas, favicon_url)

    def _load_one_logo(self, canvas: tk.Canvas, favicon_url: str):
        """Load a single logo — use memory cache first, else background."""
        if favicon_url in self._photos:
            photo = self._photos[favicon_url]
            if photo:
                # Already in memory — apply directly on main thread
                self._apply_photo(canvas, photo)
            return

        # Keep canvas id so we can find it after async load
        canvas_id = id(canvas)

        def _bg(url=favicon_url, c=canvas, cid=canvas_id):
            photo = _download_logo(url, ICON_SIZE)
            self._photos[url] = photo
            if photo:
                # Use lambda with explicit capture to avoid closure issues
                def _update(p=photo, cv=c):
                    self._apply_photo(cv, p)
                self._dispatch(_update)

        threading.Thread(target=_bg, daemon=True).start()

    def _apply_photo(self, canvas: tk.Canvas, photo):
        """Apply a loaded photo to a canvas — MUST run on main thread."""
        print(f"[Radio] _apply_photo called, photo={photo}")
        if photo is None:
            print("[Radio] photo is None")
            return
        try:
            exists = canvas.winfo_exists()
            print(f"[Radio] canvas.winfo_exists()={exists}")
            if not exists:
                return
            s = ICON_SIZE
            canvas.delete("all")
            canvas._photo = photo
            self._photo_refs.append(photo)
            canvas.create_image(s//2, s//2, image=photo, anchor="center")
            print(f"[Radio] Drew {photo.width()}x{photo.height()} on canvas")
        except Exception as e:
            print(f"[Radio] _apply_photo error: {e}")
            import traceback; traceback.print_exc()

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _play(self, station: dict):
        self._current_station = station
        self._set_status(f"▶  {station['name']}")
        try:
            if (hasattr(self._player, '_vlc_available') and
                    self._player._vlc_available):
                media = self._player._instance.media_new(station["url"])
                self._player._player.set_media(media)
                self._player._player.play()
                self._player._player.audio_set_volume(
                    self._player.get_volume())
        except Exception as e:
            print(f"[Radio] VLC error: {e}")
        self._render_current()

    def _stop(self):
        self._player.stop()
        self._current_station = None
        self._set_status("Stopped")
        self._render_current()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self, _e=None):
        q = self._search_var.get().strip()
        if not q:
            self._back_to_countries()
            return
        self._current_country = None
        self._enter_stations_view()
        self._load("", q)

    def _set_status(self, text: str):
        try:
            self._status_lbl.config(text=text)
        except Exception:
            pass

    def refresh(self):
        pass
