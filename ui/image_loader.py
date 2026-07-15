"""
JukeBox image loader — accepts BLOB bytes or a file path.
Handles PIL/ImageTk import quirks on Raspberry Pi OS.
"""

import io

_Image    = None
_ImageTk  = None
_pil_ok   = False

# ---------------------------------------------------------------------------
# Decoded-thumbnail cache
# ---------------------------------------------------------------------------
# Decoding + LANCZOS-resizing every album cover on each render is the single
# biggest cost when showing a large library (e.g. the "ALL" view). We cache
# the finished PhotoImage keyed on the source identity and target size, so a
# given cover is only decoded once per size for the life of the process.
# PhotoImages held here also stay referenced, so they never vanish from a
# canvas due to garbage collection. The cache is bounded with simple FIFO
# eviction to keep memory in check on the Pi.
_THUMB_CACHE: dict = {}
_THUMB_ORDER: list = []
_THUMB_MAX = 1500


def _cache_get(key):
    return _THUMB_CACHE.get(key)


def _cache_put(key, photo):
    if photo is None or key in _THUMB_CACHE:
        return
    _THUMB_CACHE[key] = photo
    _THUMB_ORDER.append(key)
    if len(_THUMB_ORDER) > _THUMB_MAX:
        old = _THUMB_ORDER.pop(0)
        _THUMB_CACHE.pop(old, None)


def clear_thumbnail_cache():
    """Drop all cached thumbnails (e.g. after a library rescan)."""
    _THUMB_CACHE.clear()
    _THUMB_ORDER.clear()


def _ensure_pil():
    global _Image, _ImageTk, _pil_ok
    if _pil_ok:
        return True
    try:
        from PIL import Image, ImageTk
        _Image   = Image
        _ImageTk = ImageTk
        _pil_ok  = True
        return True
    except ImportError as e:
        print(f"[ImageLoader] PIL/ImageTk unavailable: {e}")
        print("[ImageLoader] Fix: sudo apt install python3-pil.imagetk")
        return False


def blob_to_photoimage(blob: bytes, size: int):
    """
    Convert raw JPEG/PNG bytes to an ImageTk.PhotoImage at (size x size).
    Returns None on failure.
    """
    if not _ensure_pil():
        return None
    try:
        img   = _Image.open(io.BytesIO(blob)).convert("RGB")
        img   = img.resize((size, size), _Image.LANCZOS)
        return _ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"[ImageLoader] blob_to_photoimage error: {e}")
        return None


def _fit_and_pad(img, w: int, h: int, bg=(20, 20, 30)):
    """
    Resize `img` to fit within (w, h) preserving aspect ratio, then pad
    with `bg` to exactly (w, h). Prevents distortion when the source
    image's aspect ratio doesn't match the target box (e.g. a 16:9 video
    frame placed where a 2:3 movie poster is expected).
    """
    iw, ih = img.size
    if iw == 0 or ih == 0:
        return _Image.new("RGB", (w, h), bg)
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = img.resize((nw, nh), _Image.LANCZOS)
    canvas = _Image.new("RGB", (w, h), bg)
    x = (w - nw) // 2
    y = (h - nh) // 2
    canvas.paste(resized, (x, y))
    return canvas


def load_image_tk(source, size: int, height: int = 0):
    """
    Load an image from either:
      - bytes  (BLOB from DB)
      - str    (file path, legacy fallback)
    Returns an ImageTk.PhotoImage scaled to (size x size) by default,
    or (size x height) if height is provided (e.g. for movie posters).

    The image is fit within the target box preserving aspect ratio and
    padded (letterboxed) rather than stretched, so images whose aspect
    ratio doesn't match the target (e.g. a 16:9 frame in a 2:3 poster
    slot) are not distorted.

    Returns None on failure.
    """
    if not source:
        return None
    w = size
    h = height if height > 0 else size
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        key = (len(raw), hash(raw), w, h)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        if not _ensure_pil():
            return None
        try:
            img = _Image.open(io.BytesIO(raw)).convert("RGB")
            img = _fit_and_pad(img, w, h)
            photo = _ImageTk.PhotoImage(img)
            _cache_put(key, photo)
            return photo
        except Exception as e:
            print(f"[ImageLoader] blob error: {e}")
            return None
    # File path fallback
    if not _ensure_pil():
        return None
    try:
        import os
        if not os.path.exists(source):
            return None
        key = (source, os.path.getmtime(source), w, h)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        img = _Image.open(source).convert("RGB")
        img = _fit_and_pad(img, w, h)
        photo = _ImageTk.PhotoImage(img)
        _cache_put(key, photo)
        return photo
    except Exception as e:
        print(f"[ImageLoader] file load error ({source}): {e}")
        return None
