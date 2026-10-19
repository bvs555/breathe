#!/usr/bin/env python3
"""Record the phone page's spoken cues with the Mac's system voice.

Each word is spoken by `say`, trimmed of leading and trailing silence, and
compressed to a small AAC (.m4a) in docs/voice/. The page assembles these
clips into one voice track for the chosen number of sets.

Rerun after changing the system voice (System Settings > Accessibility >
Spoken Content > System Voice), then bump CACHE in docs/sw.js.
"""
import os, subprocess, sys, tempfile, wave, struct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "docs", "voice")
WORDS = {"inhale": "Inhale", "exhale": "Exhale", "rest": "Rest", "done": "Done",
         "1": "one", "2": "two", "3": "three", "4": "four", "5": "five"}
RATE = 22050
PAD = 0.03          # seconds kept before and after the word
THRESH = 0.02       # fraction of peak counted as silence

def trim(path):
    with wave.open(path, "rb") as w:
        n, sw, ch = w.getnframes(), w.getsampwidth(), w.getnchannels()
        assert sw == 2 and ch == 1, (sw, ch)
        frames = w.readframes(n)
    samples = struct.unpack("<%dh" % n, frames)
    peak = max(1, max(abs(s) for s in samples))
    lim = peak * THRESH
    first = next(i for i, s in enumerate(samples) if abs(s) > lim)
    last = len(samples) - next(i for i, s in enumerate(reversed(samples)) if abs(s) > lim)
    pad = int(PAD * RATE)
    a, b = max(0, first - pad), min(len(samples), last + pad)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % (b - a), *samples[a:b]))
    return (b - a) / RATE

os.makedirs(OUT, exist_ok=True)
for name, text in WORDS.items():
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, name + ".wav")
        subprocess.run(["say", "-o", wav, "--file-format=WAVE", f"--data-format=LEI16@{RATE}", text], check=True)
        secs = trim(wav)
        m4a = os.path.join(OUT, name + ".m4a")
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "48000", wav, m4a],
                       check=True, capture_output=True)
        print(f"{name:7s} {secs:5.2f} s  {os.path.getsize(m4a):6d} bytes")
print("clips written to", os.path.relpath(OUT))
