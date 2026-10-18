# Breathe

A breathing pacer in two forms, kept in one place:

- **Breathe.app for macOS** (`breathe_app.py`, `setup.command`): a full app with editable
  exercises, voice guidance, and a small always-on-top overlay that floats over whatever
  you are working on.
- **Breathe for iPhone** (`docs/`): a single web page with one fixed exercise, served by
  GitHub Pages at **https://bvs555.github.io/breathe/**. Add it to the Home Screen once
  and it opens full screen, with its own icon, and works offline.

## macOS app

### Setup or update (safe to rerun any time)

Open Terminal in this folder and run:

    bash setup.command

It installs the dependencies (PySide6, pyobjc) into a private environment under
`~/Library/Application Support/BreatheApp`, builds a native **Breathe.app** in
`~/Applications`, and launches it. Never run it with `sudo`.

To pin it: in Finder press Cmd+Shift+G, type `~/Applications`, press Enter, and drag
**Breathe.app** onto the Dock. One click launches it; it never opens a second copy.

After editing `breathe_app.py`, rerun `bash setup.command`, or copy the file into
`~/Applications/Breathe.app/Contents/Resources/` and relaunch the app.

### How it works

- **Home > Breathe** opens the Breathe window in a ready state: a play triangle in the
  big circle. Click the circle to start a 3-2-1 countdown and then the exercise.
- Click the circle again to pause (the arc stands still) and once more to resume. This
  works during the countdown too. After the last set the play triangle comes back.
- **Show Overlay** puts a small floating copy of the circle above every app and Space.
  It shows the same thing as the big circle, and a click on it starts, pauses or
  resumes in the same way. Drag it anywhere; Size and Transparency are sliders.
- **On play: show overlay, hide this window** (on by default) makes a click on the big
  circle bring up the overlay and hide the main window, so only the overlay stays on
  screen. Click the Dock icon to bring the window back. The red close button also
  only hides the window; quit with Cmd+Q or from the Dock.
- Voice guidance uses the macOS Eva (Premium) voice if installed, else Eva Enhanced,
  else Samantha. The Voice slider sets its volume.

### Exercises

Fixed timings (inhale, hold, exhale, hold, repeated N times) or **Fibonacci**: each
set breathes 13, 8, 5, 3, 2, 1 seconds in and out (or starting from another
Fibonacci number), with a rest of normal breathing between sets.

### Everything is remembered

All of these are saved the moment they change and restored on the next launch:
the selected exercise, each exercise's timings, Fibonacci start, rest and number of
sets, voice volume, overlay size, transparency and screen position, whether the
overlay was showing, and the pop-out toggle. Files live in
`~/Library/Application Support/BreatheApp` (`settings.json`, `exercises.json`).

## iPhone page

`docs/index.html` is self-contained: Fibonacci from 13 (13, 8, 5, 3, 2, 1 seconds
in and out), 13 seconds of rest between sets, 5 sets, a 3-2-1 countdown. No sound,
no settings. Tap to start, tap to pause or resume. Locking the phone or switching
apps pauses it; the screen stays awake while it runs.

### Put it on the Home Screen

1. Open **https://bvs555.github.io/breathe/** in Safari on the iPhone.
2. Tap Share, then **Add to Home Screen**, then Add.

The icon opens the page full screen. After the first open it works with no network:
`docs/sw.js` keeps a copy on the phone and refreshes it in the background whenever a
newer version is published.

### Updating the page

Edit `docs/index.html`, bump `CACHE` in `docs/sw.js` (for example `breathe-v2`), and
push. Phones pick up the new version on the launch after the next one.

GitHub Pages serves the `docs/` folder of the `main` branch.
