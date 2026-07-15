#!/usr/bin/env python3
"""
PulseBox — standalone rotary-encoder + wpctl diagnostic.

Run it from your normal Pi desktop session (NOT via sudo or a systemd
service — those don't have the PipeWire session that wpctl needs):

    python3 scripts/encoder_test.py

Turn the knob and press the button. Each event prints a line showing the
wpctl return code, any error, and the resulting sink volume. Ctrl-C to quit.

If this works but the PulseBox module doesn't, the problem is how the app is
launched (environment/pin factory), not the wiring. If this ALSO does
nothing on rotation, the CLK/DT pins below don't match your wiring; if it
prints rotation lines but volume never changes, wpctl can't reach the sink.

Set CLK / DT / SW to match your wiring (SW = 0 to skip the button).
"""
from signal import pause
import shutil
import subprocess

CLK, DT, SW = 17, 27, 22          # <-- your wiring
SINK        = "@DEFAULT_AUDIO_SINK@"
STEP        = "0.05"              # 5% per detent


def _vol() -> str:
    try:
        return subprocess.run(["wpctl", "get-volume", SINK],
                              capture_output=True, text=True).stdout.strip()
    except Exception as e:
        return f"(error {e})"


def _set(arg: str, tag: str):
    r = subprocess.run(["wpctl", "set-volume", "-l", "1.0", SINK, arg],
                       capture_output=True, text=True)
    print(f"{tag} -> rc={r.returncode} {r.stderr.strip()} | {_vol()}")


def up():   _set(f"{STEP}+", "CW ")
def down(): _set(f"{STEP}-", "CCW")


def mute():
    r = subprocess.run(["wpctl", "set-mute", SINK, "toggle"],
                       capture_output=True, text=True)
    print(f"BTN -> rc={r.returncode} {r.stderr.strip()} | {_vol()}")


def main():
    print(f"wpctl present: {shutil.which('wpctl') is not None}")
    print(f"start volume : {_vol()}")
    print(f"watching CLK={CLK} DT={DT} SW={SW} — turn the knob…")
    from gpiozero import RotaryEncoder, Button
    enc = RotaryEncoder(CLK, DT, wrap=False)
    enc.when_rotated_clockwise = up
    enc.when_rotated_counter_clockwise = down
    if SW:
        btn = Button(SW)
        btn.when_pressed = mute
    pause()


if __name__ == "__main__":
    main()
