"""
audio_spdif.py — keep a HiFiBerry Digi (wm8804) transmitting the Pi's own audio.

THE PROBLEM
-----------
The Digi's 'Tx Source' ALSA control selects what the wm8804 chip sends out of
the optical / coax S/PDIF port:

    'S/PDIF RX'  -> pass through the chip's own S/PDIF *input*  (silent for us)
    'AIF'        -> transmit the audio the Pi sends over I2S    (what we want)

It powers up as 'S/PDIF RX', and -- crucially -- snaps back to 'S/PDIF RX'
again every time the kernel re-probes the audio devices. That happens whenever
an HDMI display / projector connects, or its audio sink appears or disappears.
The result: PulseBox playback goes silent mid-session even though PipeWire shows
the stream perfectly routed to the card. (Volumio avoids this by re-initialising
the card in its own pipeline; a stock install does not.)

THE FIX
-------
Find whichever sound card exposes a 'Tx Source' control and force it to 'AIF'.
Call ensure_spdif_aif() once at startup AND at the top of the play path, so a
projector event can never strand you.

CARD-AGNOSTIC BY DESIGN
-----------------------
On any card WITHOUT a 'Tx Source' control -- USB DACs, I2S DAC HATs, HDMI, a
plain analogue HAT, or no HAT at all -- every function here is a safe no-op and
returns False. So this module is safe to ship in a build whose sound card is
unknown (e.g. one you're handing to someone else): it only ever acts when an
actual HiFiBerry Digi is present.
"""

from __future__ import annotations

import shutil
import subprocess

_AMIXER = shutil.which("amixer")
_CONTROL = "Tx Source"
_TARGET = "AIF"

# Resolved card NAME (stable across reboots; numbers are not).
#   None  -> not scanned yet
#   ""    -> scanned, no Digi-style card present (most setups)
#   "..." -> the card name carrying the 'Tx Source' control
_resolved_card: "str | None" = None


def _run(args, timeout=5):
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def _card_names():
    """ALSA card names from /proc/asound/cards (e.g. 'sndrpihifiberry')."""
    names = []
    try:
        with open("/proc/asound/cards", "r") as fh:
            for line in fh:
                # " 1 [sndrpihifiberry]: snd_rpi_hifiberry_digi - ..."
                if "[" in line and "]" in line:
                    names.append(line.split("[", 1)[1].split("]", 1)[0].strip())
    except OSError:
        pass
    return names


def _card_has_control(card):
    if not _AMIXER:
        return False
    return _CONTROL in _run([_AMIXER, "-c", card, "scontrols"]).stdout


def _resolve_card():
    """Return the name of the card exposing 'Tx Source', or '' if none."""
    if not _AMIXER:
        return ""
    for name in _card_names():
        if _card_has_control(name):
            return name
    return ""


def ensure_spdif_aif(log=None):
    """
    Force the Digi's Tx Source to 'AIF' if such a card exists; safe no-op
    otherwise. Cheap to call repeatedly (one amixer set after first detection).
    Returns True if AIF was asserted, False if there was nothing to do.
    """
    global _resolved_card
    if not _AMIXER:
        return False
    if _resolved_card is None:
        _resolved_card = _resolve_card()
        if log:
            if _resolved_card:
                log("S/PDIF: HiFiBerry Digi on card '%s' -> Tx Source=AIF" % _resolved_card)
            else:
                log("S/PDIF: no 'Tx Source' card present; nothing to manage")
    if not _resolved_card:
        return False
    res = _run([_AMIXER, "-c", _resolved_card, "sset", _CONTROL, _TARGET])
    if res.returncode != 0:
        # Card may have been renumbered/removed by a projector event.
        # Forget it so the next call re-detects from scratch.
        _resolved_card = None
        if log:
            log("S/PDIF: re-assert failed, will re-detect (%s)" % res.stderr.strip())
        return False
    return True


def persist_spdif_aif(log=None):
    """
    One-shot for the installer / first run: set AIF and save the ALSA state so
    it also survives a clean reboot. 'alsactl store' needs root, so run this
    under sudo from the install script. No-op on non-Digi cards.
    """
    ok = ensure_spdif_aif(log=log)
    if ok and _resolved_card:
        _run(["alsactl", "store", _resolved_card])
    return ok


if __name__ == "__main__":
    print("AIF asserted" if ensure_spdif_aif(log=print) else "no Tx Source card found")
