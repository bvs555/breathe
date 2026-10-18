"""
Breathe -- macOS edition

Converted from the Windows (PySide6) version:
  * Voice guidance uses the built-in macOS `say` command and automatically
    picks the Eva (Premium) voice if installed.
  * Settings live in ~/Library/Application Support/BreatheApp.
  * Overlays stay on top of every app, every Space, and full-screen windows.
"""

import fcntl
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, Qt, QTimer, QRect, QRectF, QSize, QPoint, QPointF, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QRegion, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


# =========================
# Storage / Models
# =========================

APP_NAME = "BreatheApp"

DEFAULT_EXERCISES = {
    "5-0-5-0": {"inhale": 5.0, "hold1": 0.0, "exhale": 5.0, "hold2": 0.0, "cycles": 108},
    "4-4-4-4": {"inhale": 4.0, "hold1": 4.0, "exhale": 4.0, "hold2": 4.0, "cycles": 11},
    "4-7-8": {"inhale": 4.0, "hold1": 7.0, "exhale": 8.0, "hold2": 0.0, "cycles": 11},
    "Fibonacci": {"mode": "fibonacci", "fib_start": 8, "fib_rest": 10, "inhale": 8.0, "hold1": 0.0, "exhale": 8.0, "hold2": 0.0, "cycles": 3},
}

# Fibonacci numbers offered as a starting point (1 appears once on purpose).
FIB_CHOICES = [1, 2, 3, 5, 8, 13, 21, 34]

def _sanitize_fib_start(v, default: int = 8) -> int:
    try:
        v = int(v)
    except Exception:
        v = default
    if v not in FIB_CHOICES:
        v = default
    return v

def _sanitize_fib_rest(v, default: int = 10) -> int:
    """Seconds of normal breathing between Fibonacci sets, 0-300."""
    try:
        v = int(v)
    except Exception:
        v = default
    return max(0, min(300, v))

def fibonacci_descending(start: int) -> List[int]:
    """Fibonacci numbers from `start` down to a single 1,
    e.g. 8 -> [8, 5, 3, 2, 1]."""
    start = _sanitize_fib_start(start)
    return [n for n in reversed(FIB_CHOICES) if n <= start]

def app_data_dir() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path

def exercises_path() -> str:
    return os.path.join(app_data_dir(), "exercises.json")

def settings_path() -> str:
    return os.path.join(app_data_dir(), "settings.json")

def load_settings() -> Dict[str, object]:
    path = settings_path()
    if not os.path.exists(path):
        return {"last_exercise": None, "last_cycles": 11, "volume": 100, "overlay_size": 160,
                "overlay_opacity": 47, "overlay_x": None, "overlay_y": None,
                "overlay_visible": False, "play_pops_out": True}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # sanitize
        last_ex = data.get("last_exercise")
        last_cycles = data.get("last_cycles", 11)
        try:
            last_cycles = int(last_cycles)
        except Exception:
            last_cycles = 11
        if last_cycles < 1:
            last_cycles = 1
        if last_cycles > 999:
            last_cycles = 999
        volume = data.get("volume", 100)
        try:
            volume = int(volume)
        except Exception:
            volume = 100
        volume = max(0, min(100, volume))
        overlay_size = data.get("overlay_size", 160)
        try:
            overlay_size = int(overlay_size)
        except Exception:
            overlay_size = 160
        overlay_size = max(80, min(400, overlay_size))
        overlay_opacity = data.get("overlay_opacity", 47)  # slider percent, 1-100
        try:
            overlay_opacity = int(overlay_opacity)
        except Exception:
            overlay_opacity = 47
        overlay_opacity = max(1, min(100, overlay_opacity))

        def _opt_int(v):
            try:
                return None if v is None else int(v)
            except Exception:
                return None
        overlay_x = _opt_int(data.get("overlay_x"))        # top-left of the overlay,
        overlay_y = _opt_int(data.get("overlay_y"))        # None until first moved
        overlay_visible = bool(data.get("overlay_visible", False))
        play_pops_out = bool(data.get("play_pops_out", True))
        return {"last_exercise": last_ex, "last_cycles": last_cycles,
                "volume": volume, "overlay_size": overlay_size,
                "overlay_opacity": overlay_opacity,
                "overlay_x": overlay_x, "overlay_y": overlay_y,
                "overlay_visible": overlay_visible,
                "play_pops_out": play_pops_out}
    except Exception:
        return {"last_exercise": None, "last_cycles": 11, "volume": 100, "overlay_size": 160,
                "overlay_opacity": 47, "overlay_x": None, "overlay_y": None,
                "overlay_visible": False, "play_pops_out": True}

def save_settings(data: Dict[str, object]) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def _sanitize_cycles(v, default: int = 11) -> int:
    try:
        v = int(v)
    except Exception:
        v = default
    if v < 1:
        v = 1
    if v > 999:
        v = 999
    return v

def load_exercises(default_cycles_fallback: int = 11) -> Dict:
    """
    Loads exercises.json and ensures each exercise has:
      inhale, hold1, exhale, hold2, cycles

    Backward compatible:
      - Older files might not have "cycles". We will add it using default_cycles_fallback.
    """
    path = exercises_path()
    if not os.path.exists(path):
        save_exercises(DEFAULT_EXERCISES)
        return dict(DEFAULT_EXERCISES)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        out: Dict[str, Dict] = {}
        for name, v in data.items():
            inhale = round(float(v.get("inhale", 4)), 1)
            hold1 = round(float(v.get("hold1", 0)), 1)
            exhale = round(float(v.get("exhale", 4)), 1)
            hold2 = round(float(v.get("hold2", 0)), 1)
            cycles = _sanitize_cycles(v.get("cycles", default_cycles_fallback), default_cycles_fallback)
            mode = "fibonacci" if str(v.get("mode", "normal")) == "fibonacci" else "normal"
            fib_start = _sanitize_fib_start(v.get("fib_start", 8))
            fib_rest = _sanitize_fib_rest(v.get("fib_rest", 10))

            out[str(name)] = {
                "mode": mode,
                "fib_start": fib_start,
                "fib_rest": fib_rest,
                "inhale": inhale,
                "hold1": hold1,
                "exhale": exhale,
                "hold2": hold2,
                "cycles": cycles,
            }

        if not out:
            out = dict(DEFAULT_EXERCISES)

        # Older files have no Fibonacci exercise: add the default one once.
        if not any(d.get("mode") == "fibonacci" for d in out.values()) and "Fibonacci" not in out:
            out["Fibonacci"] = dict(DEFAULT_EXERCISES["Fibonacci"])

        # If we upgraded an old file (missing cycles), persist the upgrade once.
        # (Safe: it only normalizes structure.)
        try:
            save_exercises(out)
        except Exception:
            pass

        return out
    except Exception:
        return dict(DEFAULT_EXERCISES)

def save_exercises(exercises: Dict) -> None:
    with open(exercises_path(), "w", encoding="utf-8") as f:
        json.dump(exercises, f, indent=2)

@dataclass
class Exercise:
    name: str
    inhale: float
    hold1: float
    exhale: float
    hold2: float
    cycles: int = 11
    mode: str = "normal"      # "normal" or "fibonacci"
    fib_start: int = 8        # starting Fibonacci number (fibonacci mode only)
    fib_rest: int = 10        # seconds of normal breathing between sets (fibonacci mode only)

    @property
    def is_fibonacci(self) -> bool:
        return self.mode == "fibonacci"

    def to_dict(self) -> Dict:
        return {
            "mode": self.mode,
            "fib_start": self.fib_start,
            "fib_rest": self.fib_rest,
            "inhale": self.inhale,
            "hold1": self.hold1,
            "exhale": self.exhale,
            "hold2": self.hold2,
            "cycles": self.cycles,
        }

    @staticmethod
    def from_dict(name: str, d: Dict) -> "Exercise":
        return Exercise(
            name=name,
            inhale=round(float(d.get("inhale", 4)), 1),
            hold1=round(float(d.get("hold1", 0)), 1),
            exhale=round(float(d.get("exhale", 4)), 1),
            hold2=round(float(d.get("hold2", 0)), 1),
            cycles=_sanitize_cycles(d.get("cycles", 11), 11),
            mode="fibonacci" if str(d.get("mode", "normal")) == "fibonacci" else "normal",
            fib_start=_sanitize_fib_start(d.get("fib_start", 8)),
            fib_rest=_sanitize_fib_rest(d.get("fib_rest", 10)),
        )

class Voice:
    """
    Text-to-speech via the built-in macOS `say` command.

    Automatically prefers the Ava (Premium) voice if installed
    (System Settings > Accessibility > Spoken Content > System Voice).
    If none of the preferred voices are found, no -v flag is passed, so `say`
    uses whatever System Voice is selected in Spoken Content settings.
    """

    PREFERRED = ("ava (premium)", "eva (premium)", "ava (enhanced)", "eva (enhanced)",
                 "ava", "eva")

    def __init__(self):
        self.enabled = sys.platform == "darwin"
        self.volume = 1.0          # 0.0-1.0, app-level (via [[volm]] speech command)
        self.rate_wpm = 160        # slightly slower than default (~175) = more soothing
        self._proc: Optional[subprocess.Popen] = None
        self.voice_name = self._pick_voice() if self.enabled else None

    @staticmethod
    def _available_voices() -> List[str]:
        try:
            out = subprocess.run(
                ["say", "-v", "?"],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except Exception:
            return []
        names = []
        for line in out.splitlines():
            # e.g. "Eva (Premium)       en_US    # Hello! My name is Eva."
            m = re.match(r"^(.+?)\s{2,}[a-zA-Z]{2,3}[_-]", line)
            if m:
                names.append(m.group(1).strip())
        return names

    def _pick_voice(self) -> Optional[str]:
        names = self._available_voices()
        lower = {n.lower(): n for n in names}
        for want in self.PREFERRED:
            if want in lower:
                return lower[want]
        # Fall back to the System Voice chosen in
        # System Settings > Accessibility > Spoken Content (no -v flag).
        return None

    def say(self, text: str):
        if not self.enabled:
            return
        self._stop_current()  # never overlap phases
        message = f"[[volm {self.volume:.2f}]] {text}"
        cmd = ["say", "-r", str(self.rate_wpm)]
        if self.voice_name:
            cmd += ["-v", self.voice_name]
        cmd.append(message)
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            self.enabled = False

    def _stop_current(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None

    def set_volume(self, percent: int):
        """Set TTS volume. percent: 0-100 (app-level only, not system volume)."""
        self.volume = max(0.0, min(1.0, percent / 100.0))

    def shutdown(self):
        self._stop_current()

# =========================
# macOS window helpers
# =========================

_MAC_ACTIVITY_TOKEN = None  # keeps the App Nap exemption alive for the app lifetime

def mac_disable_app_nap():
    """Stop macOS App Nap from throttling the breathing animation timers."""
    global _MAC_ACTIVITY_TOKEN
    if sys.platform != "darwin":
        return
    try:
        from Foundation import (
            NSProcessInfo,
            NSActivityUserInitiatedAllowingIdleSystemSleep,
        )
        _MAC_ACTIVITY_TOKEN = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            NSActivityUserInitiatedAllowingIdleSystemSleep, "Breathing pacer running"
        )
    except Exception:
        pass

def mac_pin_overlay_everywhere(widget):
    """
    Keep an overlay window visible on top of everything:
      * every Space / desktop, including full-screen apps
      * even while Breathe is not the active app
    Uses pyobjc (installed by setup.command); silently skipped if unavailable.
    Must be called after the widget is shown (needs a native window handle).
    """
    if sys.platform != "darwin":
        return
    try:
        import objc
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSStatusWindowLevel,
        )
        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        if window is None:
            return
        window.setHidesOnDeactivate_(False)
        window.setCollectionBehavior_(
            window.collectionBehavior()
            | NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        window.setLevel_(NSStatusWindowLevel)

        # Make the overlay a NON-ACTIVATING panel: clicking it must never
        # activate the app (which is what used to make the hidden main
        # window pop back up when pausing/resuming from the overlay).
        try:
            from AppKit import NSPanel
            if window.isKindOfClass_(NSPanel):
                NSWindowStyleMaskNonactivatingPanel = 1 << 7
                window.setStyleMask_(
                    window.styleMask() | NSWindowStyleMaskNonactivatingPanel
                )
                window.setBecomesKeyOnlyIfNeeded_(True)
        except Exception:
            pass
    except Exception:
        pass

_MAC_DOCK_DELEGATE = None        # keeps our delegate object alive for the app lifetime
_MAC_DOCK_DELEGATE_CLASS = None  # the Objective-C class is created only once
_MAC_DOCK_CALLBACK = None        # Python function to run on a Dock-icon click

def mac_install_dock_click_handler(callback) -> bool:
    """
    Run `callback` when the user clicks the app's Dock icon.

    macOS reports a Dock-icon click to the app delegate as
    applicationShouldHandleReopen:hasVisibleWindows:. Clicking an overlay
    window never sends this message, so it is a clean "Dock click" signal
    (unlike plain app activation, which an overlay click can also cause).

    Qt owns the NSApplication delegate (QCocoaApplicationDelegate). Its own
    applicationShouldHandleReopen:hasVisibleWindows: first hands the call to
    a "reflection delegate" if one is set (see qcocoaapplicationdelegate.mm),
    so we register a tiny delegate there. Returns True if installed.
    """
    global _MAC_DOCK_DELEGATE, _MAC_DOCK_DELEGATE_CLASS, _MAC_DOCK_CALLBACK
    if sys.platform != "darwin":
        return False
    try:
        import objc
        from AppKit import NSApplication, NSObject

        qt_delegate = NSApplication.sharedApplication().delegate()
        if qt_delegate is None:
            return False
        if not qt_delegate.respondsToSelector_("setReflectionDelegate:"):
            return False

        _MAC_DOCK_CALLBACK = callback

        if _MAC_DOCK_DELEGATE_CLASS is None:
            # Objective-C type encoding: returns BOOL, args (self, _cmd, id, BOOL)
            signature = (objc._C_NSBOOL + objc._C_ID + objc._C_SEL
                         + objc._C_ID + objc._C_NSBOOL)

            class BreatheDockDelegate(NSObject):
                @objc.typedSelector(signature)
                def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, has_visible):
                    try:
                        if _MAC_DOCK_CALLBACK is not None:
                            _MAC_DOCK_CALLBACK()
                    except Exception:
                        pass
                    # YES = same answer Qt itself gives, so AppKit's default
                    # reopen handling stays exactly as it was.
                    return True

            _MAC_DOCK_DELEGATE_CLASS = BreatheDockDelegate

        _MAC_DOCK_DELEGATE = _MAC_DOCK_DELEGATE_CLASS.alloc().init()
        qt_delegate.setReflectionDelegate_(_MAC_DOCK_DELEGATE)
        return True
    except Exception:
        return False

# =========================
# UI Helpers
# =========================

def make_pill_button(text: str, big: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(60 if big else 52)
    btn.setStyleSheet("""
        QPushButton {
            color: white;
            background-color: rgba(50,50,50,0.65);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 26px;
            padding-left: 18px;
            padding-right: 18px;
            font-size: 22px;
        }
        QPushButton:hover { background-color: rgba(70,70,70,0.75); }
        QPushButton:pressed { background-color: rgba(90,90,90,0.75); }
    """)
    if big:
        btn.setStyleSheet(btn.styleSheet() + "QPushButton{font-size: 34px; font-weight: 700; border-radius: 30px;}")
        btn.setMinimumHeight(80)
    return btn

def make_icon_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(64, 44)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setStyleSheet("""
        QPushButton {
            color: white;
            background-color: rgba(50,50,50,0.65);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 20px;
            font-size: 22px;
            outline: none;
            padding: 0px;
        }
        QPushButton:hover { background-color: rgba(70,70,70,0.75); border: 1px solid rgba(255,255,255,0.08); }
        QPushButton:pressed { background-color: rgba(90,90,90,0.75); border: 1px solid rgba(255,255,255,0.08); }
        QPushButton:focus { background-color: rgba(50,50,50,0.65); border: 1px solid rgba(255,255,255,0.08); outline: none; }
        QPushButton:default { background-color: rgba(50,50,50,0.65); border: 1px solid rgba(255,255,255,0.08); }
    """)
    return btn

class LabeledSlider(QWidget):
    """Slider with 0.1-second precision. The value display is clickable/editable."""
    def __init__(self, label: str, min_v=0.0, max_v=30.0, init=4.0):
        super().__init__()
        self._label = QLabel(label)
        self._label.setStyleSheet("color: white; font-size: 22px;")

        # Editable value display (QLineEdit instead of QLabel)
        self._value_edit = QLineEdit(f"{init:.1f}s")
        self._value_edit.setFixedSize(130, 40)
        self._value_edit.setAlignment(Qt.AlignCenter)
        self._value_edit.setStyleSheet(
            "QLineEdit { color: white; font-size: 22px; "
            "background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); "
            "border-radius: 8px; padding: 0px 4px; }"
            "QLineEdit:focus { border: 1px solid rgba(255,255,255,0.45); }"
        )
        self._value_edit.editingFinished.connect(self._on_edit_finished)
        self._value_edit.setToolTip("Click to type a value")

        # Internal slider uses integers: value * 10 (so 4.5 → 45)
        self._min_v = min_v
        self._max_v = max_v

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(int(round(min_v * 10)))
        self.slider.setMaximum(int(round(max_v * 10)))
        self.slider.setValue(int(round(init * 10)))
        self.slider.setStyleSheet("""
            QSlider { background: transparent; }
            QSlider::groove:horizontal { height: 6px; background: rgba(255,255,255,0.15); border-radius: 3px; }
            QSlider::sub-page:horizontal { background: rgba(255,255,255,0.55); border-radius: 3px; }
            QSlider::handle:horizontal { width: 34px; margin: -14px 0; border-radius: 17px; background: white; }
        """)
        # The handle is 34 px tall (6 px groove + 14 px above and below), so the
        # slider itself must be at least that tall or the handle is clipped.
        self.slider.setFixedHeight(36)
        self.slider.valueChanged.connect(self._on_slider_change)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._label)
        top.addStretch(1)
        top.addWidget(self._value_edit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.slider)
        # Label row (40) + spacing (6) + slider (36): never let a layout squeeze below this.
        self.setMinimumHeight(40 + 6 + 36)

    def _on_slider_change(self, v_int: int):
        real = v_int / 10.0
        self._value_edit.setText(f"{real:.1f}s")

    def _on_edit_finished(self):
        text = self._value_edit.text().strip().rstrip('s').strip()
        try:
            val = round(float(text), 1)
            val = max(self._min_v, min(self._max_v, val))
        except ValueError:
            val = self.value()
        self.slider.setValue(int(round(val * 10)))
        self._value_edit.setText(f"{val:.1f}s")

    def value(self) -> float:
        return self.slider.value() / 10.0

    def setValue(self, v: float):
        v = round(float(v), 1)
        self.slider.setValue(int(round(v * 10)))
        self._value_edit.setText(f"{v:.1f}s")

# =========================
# Create Exercise Dialog (optional helper)
# =========================

class ExerciseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Exercise")
        self.setModal(True)
        self.setStyleSheet("background-color: #0b0b0b; color: white;")
        self.resize(420, 260)

        self.name_edit = QLineEdit("")
        self.name_edit.setPlaceholderText("e.g., 4-4-4-4")
        self.name_edit.setStyleSheet("padding: 8px; font-size: 16px;")

        def make_spin(init: float):
            s = QDoubleSpinBox()
            s.setRange(0.0, 60.0)
            s.setSingleStep(0.1)
            s.setDecimals(1)
            s.setValue(init)
            s.setStyleSheet("padding: 6px; font-size: 16px;")
            return s

        self.inhale = make_spin(4.0)
        self.hold1 = make_spin(0.0)
        self.exhale = make_spin(4.0)
        self.hold2 = make_spin(0.0)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["Inhale / Hold / Exhale / Hold", "Fibonacci (descending)"])
        self.type_combo.setStyleSheet("padding: 6px; font-size: 16px;")

        self.fib_combo = QComboBox()
        self.fib_combo.addItems([str(n) for n in FIB_CHOICES])
        self.fib_combo.setCurrentText("8")
        self.fib_combo.setStyleSheet("padding: 6px; font-size: 16px;")

        self.rest_spin = QSpinBox()
        self.rest_spin.setRange(0, 300)
        self.rest_spin.setValue(10)
        self.rest_spin.setSuffix(" s")
        self.rest_spin.setStyleSheet("padding: 6px; font-size: 16px;")

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Type", self.type_combo)
        self.fib_label = QLabel("Starting Fibonacci number")
        form.addRow(self.fib_label, self.fib_combo)
        self.rest_label = QLabel("Rest between sets")
        form.addRow(self.rest_label, self.rest_spin)
        self.timing_labels = [QLabel("Inhale (sec)"), QLabel("Hold (sec)"), QLabel("Exhale (sec)"), QLabel("Hold (sec)")]
        for lbl, spin in zip(self.timing_labels, (self.inhale, self.hold1, self.exhale, self.hold2)):
            form.addRow(lbl, spin)

        def on_type_changed(_idx):
            fib = self.type_combo.currentIndex() == 1
            self.fib_label.setVisible(fib)
            self.fib_combo.setVisible(fib)
            self.rest_label.setVisible(fib)
            self.rest_spin.setVisible(fib)
            for lbl, spin in zip(self.timing_labels, (self.inhale, self.hold1, self.exhale, self.hold2)):
                lbl.setVisible(not fib)
                spin.setVisible(not fib)
        self.type_combo.currentIndexChanged.connect(on_type_changed)
        on_type_changed(0)

        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        for b in (btn_save, btn_cancel):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton { padding: 10px; border-radius: 10px; background: rgba(255,255,255,0.12); color: white; font-size: 16px; }
                QPushButton:hover { background: rgba(255,255,255,0.20); }
            """)
        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_cancel)
        row.addWidget(btn_save)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(row)

    def get_exercise(self) -> Exercise:
        name = self.name_edit.text().strip()
        return Exercise(
            name=name,
            inhale=round(self.inhale.value(), 1),
            hold1=round(self.hold1.value(), 1),
            exhale=round(self.exhale.value(), 1),
            hold2=round(self.hold2.value(), 1),
            cycles=11,
            mode="fibonacci" if self.type_combo.currentIndex() == 1 else "normal",
            fib_start=_sanitize_fib_start(self.fib_combo.currentText()),
            fib_rest=_sanitize_fib_rest(self.rest_spin.value()),
        )

# =========================
# Breathing Circle Widget (Main Window)
# =========================

class BreathCircle(QWidget):
    """
    - Inhale/Exhale: 360° arc progress (fills/empties within phase duration)
    - Hold / countdown: full ring + inner horizontal progress bar
    - Ready / Done: a play triangle in the centre instead of the phase text
    A click inside the circle emits `clicked` (start / pause / resume).
    """
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.phase_text = "Inhale"
        self.cycles_left_text = ""
        self.progress = 0.0  # 0..1
        self.is_hold = False
        self.show_play = False
        self.seconds_text = ""   # length of this breath (13, 8, 5 ...) for Fibonacci
        self._pressed_inside = False
        self.setMinimumSize(QSize(520, 520))
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)  # hand cursor only while over the circle

    def set_state(self, phase: str, progress: float, cycles_left_text: str, is_hold: bool,
                  show_play: bool = False, seconds_text: str = ""):
        self.phase_text = phase
        self.progress = max(0.0, min(1.0, float(progress)))
        self.cycles_left_text = cycles_left_text
        self.is_hold = is_hold
        self.show_play = show_play
        self.seconds_text = seconds_text
        self.update()

    # ---------- Geometry ----------
    def _circle_rect(self) -> QRectF:
        w = self.width()
        h = self.height()
        circle_d = min(w, h) * 0.82
        return QRectF(w / 2 - circle_d / 2, h / 2 - circle_d / 2, circle_d, circle_d)

    def _inside_circle(self, pos: QPointF) -> bool:
        rect = self._circle_rect()
        r = rect.width() / 2
        dx = pos.x() - rect.center().x()
        dy = pos.y() - rect.center().y()
        return dx * dx + dy * dy <= r * r

    # ---------- Mouse: the circle itself is the start / pause / resume button ----------
    def mouseMoveEvent(self, event):
        inside = self._inside_circle(event.position())
        self.setCursor(Qt.PointingHandCursor if inside else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._inside_circle(event.position()):
            self._pressed_inside = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed_inside:
            self._pressed_inside = False
            if self._inside_circle(event.position()):
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # A fast click-click arrives as a double-click. Treat it as another
        # normal click so quick pause -> resume always works.
        self.mousePressEvent(event)

    # ---------- Painting ----------
    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        side = min(w, h)
        cx = w / 2
        cy = h / 2

        rect = self._circle_rect()
        circle_d = rect.width()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # circle fill
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(40, 40, 40, 240))
        painter.drawEllipse(rect)

        # ring / arc
        arc_rect = rect.adjusted(circle_d*0.06, circle_d*0.06, -circle_d*0.06, -circle_d*0.06)
        pen = QPen(QColor(245, 245, 245), max(10, int(side*0.018)))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        if self.is_hold:
            painter.drawEllipse(arc_rect)
        else:
            # 360° arc. Start at top (90°) and go clockwise.
            span = 360.0 * self.progress
            painter.drawArc(arc_rect, int(90 * 16), int(-span * 16))

        if self.show_play:
            # play triangle (ready / done): click the circle to start
            tri_h = circle_d * 0.24
            tri_w = tri_h * 0.87
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(245, 245, 245))
            painter.drawPolygon(QPolygonF([
                QPointF(cx - tri_w / 3, cy - tri_h / 2),
                QPointF(cx + 2 * tri_w / 3, cy),
                QPointF(cx - tri_w / 3, cy + tri_h / 2),
            ]))
        else:
            # phase text
            painter.setPen(QColor(245, 245, 245))
            font = QFont("Helvetica Neue", int(side*0.08))
            font.setWeight(QFont.Medium)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, self.phase_text)

        # hold bar
        if self.is_hold:
            bar_w = circle_d * 0.55
            bar_h = max(10, circle_d * 0.03)
            bar_x = cx - bar_w/2
            bar_y = cy + circle_d*0.08

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255,255,255,50))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), bar_h/2, bar_h/2)

            painter.setBrush(QColor(255,255,255,160))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w * self.progress, bar_h), bar_h/2, bar_h/2)

        # this breath's length (Fibonacci), between the phase word and the sets line
        if self.seconds_text and not self.show_play:
            painter.setPen(QColor(245, 245, 245, 215))
            font_s = QFont("Helvetica Neue", int(side*0.06))
            font_s.setWeight(QFont.Medium)
            painter.setFont(font_s)
            painter.drawText(QRectF(rect.x(), cy + circle_d*0.08, rect.width(), circle_d*0.10),
                             Qt.AlignHCenter | Qt.AlignVCenter, self.seconds_text)

        # cycles left / run length / "Done"
        painter.setPen(QColor(220, 220, 220))
        font2 = QFont("Helvetica Neue", int(side*0.03))
        painter.setFont(font2)
        painter.drawText(
            QRectF(rect.x(), rect.y() + circle_d*0.725, rect.width(), rect.height()*0.25),
            Qt.AlignHCenter | Qt.AlignTop,
            self.cycles_left_text
        )

        painter.end()


# =========================
# Overlay Window (Always-on-Top Mini Breathing Circle)
# =========================

OVERLAY_SIZE = 160  # pixels — small enough for corner, large enough to see

class OverlayWindow(QWidget):
    """
    A small, frameless, always-on-top, draggable circular window that
    mirrors the main circle: the same arc/ring, the sets-left number or the
    countdown digit in the centre, and a play triangle when ready or done.
    Background alpha is controlled externally via set_bg_alpha().
    Single-click (no drag) does what a click on the main circle does
    (start / pause / resume) via a callback wired by MainWindow. No pause
    icon is drawn — the frozen/moving animation itself is the feedback.
    Hide the overlay from the app's "Hide Overlay" button.
    """
    def __init__(self, parent=None):
        super().__init__(None)  # No parent — independent top-level window

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                      # no extra Dock entry
            | Qt.WindowDoesNotAcceptFocus  # clicks don't steal focus from your work
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)  # visible while app inactive

        self._size = OVERLAY_SIZE
        self.setFixedSize(self._size, self._size)

        # Start at bottom-right of screen (MainWindow may move it to the saved spot)
        self.move_to_default_corner()

        # State mirrored from main window
        self.progress = 0.0
        self.is_hold = False
        self.center_text = ""   # breath length (Fibonacci) or sets left, or the countdown digit
        self.sub_text = ""      # small sets-left number under the centre text (Fibonacci)
        self.show_play = False  # ready / done: a play triangle instead of text

        # Background alpha: 3-255.  Min 3 (~1%) so overlay is always draggable.
        self.bg_alpha = 120

        # For dragging / click detection
        self._drag_pos: Optional[QPoint] = None
        self._press_pos: Optional[QPoint] = None
        self._press_frame_pos: Optional[QPoint] = None
        self._click_callback = None  # set by MainWindow -> start / pause / resume
        self._move_callback = None   # set by MainWindow -> remembers the position

        # Mouse tracking keeps hover events flowing, which lets MainWindow
        # tell overlay interactions apart from Dock-icon clicks.
        self.setMouseTracking(True)

    def set_click_callback(self, cb):
        self._click_callback = cb

    def set_moved_callback(self, cb):
        """Called after a drag (or a resize) left the overlay somewhere new."""
        self._move_callback = cb

    def restore_position(self, x: int, y: int) -> bool:
        """Move back to a saved top-left, but only if a usable part of the
        overlay (40x40 px) would land on some screen; a spot on a monitor
        that is no longer connected is ignored. Returns True when moved."""
        rect = QRect(int(x), int(y), self._size, self._size)
        for screen in QApplication.screens():
            inter = rect.intersected(screen.availableGeometry())
            if inter.width() >= 40 and inter.height() >= 40:
                self.move(rect.topLeft())
                return True
        return False

    def set_state(self, progress: float, is_hold: bool, center_text: str = "",
                  show_play: bool = False, sub_text: str = ""):
        """Mirror the main circle: same arc/ring, a short centre text (the
        breath length, sets left, or the countdown digit), an optional small
        sub text (sets left) under it, or a play triangle."""
        self.progress = max(0.0, min(1.0, float(progress)))
        self.is_hold = is_hold
        self.center_text = center_text
        self.show_play = show_play
        self.sub_text = sub_text
        self.update()

    def set_bg_alpha(self, alpha: int):
        """Set background darkness. Clamped to [3, 255] so it's always draggable."""
        self.bg_alpha = max(3, min(255, alpha))
        self.update()

    def set_size(self, px: int):
        """Resize the overlay (80-400 px), keeping it centered in place."""
        px = max(80, min(400, int(px)))
        if px == self._size:
            return
        # Float centre: integer rounding used to let the overlay creep up-left
        # by a pixel at every step while the size slider was being dragged.
        top_left = self.frameGeometry().topLeft()
        cx = top_left.x() + self._size / 2.0
        cy = top_left.y() + self._size / 2.0
        self._size = px
        self.setFixedSize(px, px)
        self.move(int(round(cx - px / 2.0)), int(round(cy - px / 2.0)))
        self.update()

    def move_to_default_corner(self):
        """Bottom-right of the primary screen, 30 px in, for the current size."""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.right() - self._size - 30, geo.bottom() - self._size - 30)

    def showEvent(self, event):
        super().showEvent(event)
        mac_pin_overlay_everywhere(self)

    # ---------- Painting ----------
    def paintEvent(self, event):
        size = self._size
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        cx = size / 2
        cy = size / 2
        circle_d = size * 0.90

        rect = QRectF(cx - circle_d/2, cy - circle_d/2, circle_d, circle_d)

        # Background circle — alpha controlled by Run screen slider
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 20, self.bg_alpha))
        painter.drawEllipse(rect)

        # Arc / ring — always fully opaque white
        arc_rect = rect.adjusted(circle_d*0.07, circle_d*0.07, -circle_d*0.07, -circle_d*0.07)
        pen = QPen(QColor(245, 245, 245, 255), max(4, int(size * 0.025)))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        if self.is_hold:
            painter.drawEllipse(arc_rect)
        else:
            span = 360.0 * self.progress
            painter.drawArc(arc_rect, int(90 * 16), int(-span * 16))

        # Hold bar (mini) — always visible
        if self.is_hold:
            bar_w = circle_d * 0.50
            bar_h = max(3, circle_d * 0.03)
            bar_x = cx - bar_w / 2
            bar_y = cy + circle_d * 0.10

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 50))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), bar_h / 2, bar_h / 2)
            painter.setBrush(QColor(255, 255, 255, 180))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w * self.progress, bar_h), bar_h / 2, bar_h / 2)

        if self.show_play:
            # Play triangle (ready / done), same as the main circle
            tri_h = circle_d * 0.30
            tri_w = tri_h * 0.87
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(245, 245, 245, 230))
            painter.drawPolygon(QPolygonF([
                QPointF(cx - tri_w / 3, cy - tri_h / 2),
                QPointF(cx + 2 * tri_w / 3, cy),
                QPointF(cx - tri_w / 3, cy + tri_h / 2),
            ]))
        elif self.center_text:
            # Breath length (Fibonacci) or sets left while running, or the countdown digit
            painter.setPen(QColor(245, 245, 245, 220))
            font = QFont("Helvetica Neue", int(size * 0.16))
            font.setWeight(QFont.Medium)
            painter.setFont(font)
            if self.sub_text:
                # breath length a little smaller and higher, sets left readable under it
                font_c = QFont("Helvetica Neue", int(size * 0.14))
                font_c.setWeight(QFont.Medium)
                painter.setFont(font_c)
                painter.drawText(rect.adjusted(0, -size * 0.08, 0, -size * 0.08), Qt.AlignCenter, self.center_text)
                painter.setPen(QColor(245, 245, 245, 190))
                font_s = QFont("Helvetica Neue", int(size * 0.105))
                painter.setFont(font_s)
                painter.drawText(QRectF(rect.x(), cy + size * 0.09, rect.width(), size * 0.15),
                                 Qt.AlignHCenter | Qt.AlignVCenter, self.sub_text)
            else:
                painter.drawText(rect, Qt.AlignCenter, self.center_text)

        painter.end()

    # ---------- Dragging / click-to-pause ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPosition().toPoint()
            self._press_frame_pos = self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton
                and self._drag_pos is not None
                and self._press_pos is not None):
            # Only treat it as a click if the mouse barely moved (not a drag)
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved < 8 and self._click_callback:
                self._click_callback()
            # Remember the new spot whenever the overlay actually moved
            if (self._press_frame_pos is not None
                    and self.frameGeometry().topLeft() != self._press_frame_pos
                    and self._move_callback):
                self._move_callback()
        self._drag_pos = None
        self._press_pos = None
        self._press_frame_pos = None

    def mouseDoubleClickEvent(self, event):
        # Fast click-click lands here as a double-click. Treat it as another
        # normal click so quick pause -> play toggling always works.
        # (The overlay is hidden via the app's "Hide Overlay" button,
        # NOT by double-clicking, so a stray double-click can't close it.)
        self.mousePressEvent(event)


# =========================
# Screens
# =========================

class HomeScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: black;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(70, 80, 70, 80)
        layout.setSpacing(18)

        self.btn_breathe = make_pill_button("Breathe", big=True)
        self.btn_exercises = make_pill_button("Exercises")
        self.btn_settings = make_pill_button("Settings")
        self.btn_benefits = make_pill_button("Benefits")
        self.btn_more = make_pill_button("More")

        layout.addStretch(1)
        layout.addWidget(self.btn_breathe)
        layout.addWidget(self.btn_exercises)
        layout.addWidget(self.btn_settings)
        layout.addWidget(self.btn_benefits)
        layout.addWidget(self.btn_more)
        layout.addStretch(2)

class ExercisesScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: black; color: white;")

        self.back_btn = make_icon_button("←")
        self.menu_btn = make_icon_button("☰")

        top = QHBoxLayout()
        top.addWidget(self.back_btn)
        title = QLabel("Exercises")
        title.setStyleSheet("color: white; font-size: 26px; font-weight: 600;")
        top.addSpacing(18)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.menu_btn)

        self.combo = QComboBox()
        self.combo.setStyleSheet("""
            QComboBox { background: rgba(255,255,255,0.10); padding: 10px; border-radius: 12px; font-size: 20px; color: white; }
            QComboBox QAbstractItemView { background: #111; color: white; selection-background-color: #333; }
        """)
        self.combo.setMinimumHeight(48)

        self.sl_inhale = LabeledSlider("↗  Inhale", 0.0, 30.0, 4.0)
        self.sl_hold1 = LabeledSlider("→  Hold", 0.0, 30.0, 0.0)
        self.sl_exhale = LabeledSlider("↘  Exhale", 0.0, 30.0, 4.0)
        self.sl_hold2 = LabeledSlider("→  Hold", 0.0, 30.0, 0.0)

        card = QWidget()
        card.setStyleSheet("background-color: rgba(255,255,255,0.06); border-radius: 18px;")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(16)
        card_layout.addWidget(self.sl_inhale)
        card_layout.addWidget(self.sl_hold1)
        card_layout.addWidget(self.sl_exhale)
        card_layout.addWidget(self.sl_hold2)
        self.timing_card = card

        # Fibonacci card: shown instead of the sliders for a Fibonacci exercise
        self.fib_card = QWidget()
        self.fib_card.setStyleSheet("background-color: rgba(255,255,255,0.06); border-radius: 18px;")
        fib_layout = QVBoxLayout(self.fib_card)
        fib_layout.setContentsMargins(18, 18, 18, 18)
        fib_layout.setSpacing(12)
        fib_row = QHBoxLayout()
        fib_label = QLabel("Starting Fibonacci number")
        fib_label.setStyleSheet("color: white; font-size: 22px;")
        self.fib_combo = QComboBox()
        self.fib_combo.addItems([str(n) for n in FIB_CHOICES])
        self.fib_combo.setCurrentText("8")
        self.fib_combo.setMinimumWidth(110)
        self.fib_combo.setStyleSheet("""
            QComboBox { background: rgba(255,255,255,0.10); padding: 8px; border-radius: 12px; font-size: 20px; color: white; }
            QComboBox QAbstractItemView { background: #111; color: white; selection-background-color: #333; }
        """)
        fib_row.addWidget(fib_label)
        fib_row.addStretch(1)
        fib_row.addWidget(self.fib_combo)
        fib_layout.addLayout(fib_row)

        # Rest between sets: normal breathing before the next set starts at the top again
        rest_row = QHBoxLayout()
        rest_label = QLabel("Rest between sets")
        rest_label.setStyleSheet("color: white; font-size: 22px;")
        self.fib_rest_spin = QSpinBox()
        self.fib_rest_spin.setRange(0, 300)
        self.fib_rest_spin.setValue(10)
        self.fib_rest_spin.setSuffix(" s")
        self.fib_rest_spin.setMinimumWidth(110)
        self.fib_rest_spin.setStyleSheet("padding: 8px; font-size: 20px; background: rgba(255,255,255,0.10); border-radius: 12px; color: white;")
        rest_row.addWidget(rest_label)
        rest_row.addStretch(1)
        rest_row.addWidget(self.fib_rest_spin)
        fib_layout.addLayout(rest_row)

        self.fib_sequence_label = QLabel("")
        self.fib_sequence_label.setWordWrap(True)
        self.fib_sequence_label.setStyleSheet("color: rgba(255,255,255,0.75); font-size: 17px;")
        fib_layout.addWidget(self.fib_sequence_label)
        self.fib_card.hide()

        repeat_card = QWidget()
        repeat_card.setStyleSheet("background-color: rgba(255,255,255,0.06); border-radius: 18px;")
        repeat_layout = QHBoxLayout(repeat_card)
        repeat_layout.setContentsMargins(18, 18, 18, 18)

        self.cycles_label = QLabel("Repeat Exercise")
        self.cycles_label.setStyleSheet("color: white; font-size: 22px;")
        self.cycles_spin = QSpinBox()
        self.cycles_spin.setRange(1, 999)
        self.cycles_spin.setValue(11)
        self.cycles_spin.setStyleSheet("padding: 8px; font-size: 20px; background: rgba(255,255,255,0.10); border-radius: 12px; color: white;")

        repeat_layout.addWidget(self.cycles_label)
        repeat_layout.addStretch(1)
        repeat_layout.addWidget(self.cycles_spin)

        self.length_label = QLabel("Exercise Length: —")
        self.length_label.setStyleSheet("color: rgba(255,255,255,0.75); font-size: 18px;")

        self.btn_start = make_pill_button("Start")
        self.btn_start.setMinimumHeight(60)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)
        layout.addLayout(top)
        layout.addSpacing(8)

        layout.addWidget(QLabel("Selected Exercise"))
        layout.itemAt(layout.count()-1).widget().setStyleSheet("color: rgba(255,255,255,0.70); font-size: 18px;")
        layout.addWidget(self.combo)
        layout.addWidget(card)
        layout.addWidget(self.fib_card)
        layout.addWidget(repeat_card)
        layout.addWidget(self.length_label, alignment=Qt.AlignHCenter)
        layout.addStretch(1)
        layout.addWidget(self.btn_start)

    def set_fibonacci_mode(self, fib: bool):
        """Swap the timing sliders for the Fibonacci card and relabel the repeat count."""
        # Hide first, then show, so both cards are never visible at the same
        # time (that would momentarily inflate the window's minimum height).
        if fib:
            self.timing_card.hide()
            self.fib_card.show()
        else:
            self.fib_card.hide()
            self.timing_card.show()
        self.cycles_label.setText("Number of Sets" if fib else "Repeat Exercise")

def make_slider_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 13px; background: transparent;")
    return lbl


class RunScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: black; color: white;")

        self.back_btn = make_icon_button("←")

        top = QHBoxLayout()
        top.addWidget(self.back_btn)
        top.addStretch(1)

        self.exercise_combo = QComboBox()
        self.exercise_combo.setStyleSheet("""
            QComboBox { background: rgba(255,255,255,0.10); padding: 10px; border-radius: 12px; font-size: 18px; color: white; }
            QComboBox QAbstractItemView { background: #111; color: white; selection-background-color: #333; }
        """)
        self.exercise_combo.setMinimumHeight(42)

        # The circle is the only start / pause / resume control: click it.
        self.circle = BreathCircle()

        # --- Volume slider (app-level TTS volume only) ---
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(100)
        self.volume_slider.setMinimumHeight(30)
        self.volume_slider.setToolTip("Voice volume")
        self.volume_slider.setStyleSheet("""
            QSlider { background: transparent; }
            QSlider::groove:horizontal { height: 6px; background: rgba(255,255,255,0.18); border-radius: 3px; }
            QSlider::sub-page:horizontal { background: rgba(255,255,255,0.55); border-radius: 3px; }
            QSlider::handle:horizontal { width: 26px; margin: -10px 0; border-radius: 13px; background: white; }
        """)
        # --- End volume slider ---

        # --- Overlay toggle button ---
        self.overlay_btn = QPushButton("Show Overlay")
        self.overlay_btn.setCursor(Qt.PointingHandCursor)
        self.overlay_btn.setMinimumHeight(42)
        self.overlay_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background-color: rgba(50,50,50,0.65);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 16px;
                padding-left: 18px;
                padding-right: 18px;
                font-size: 16px;
            }
            QPushButton:hover { background-color: rgba(70,70,70,0.75); }
            QPushButton:pressed { background-color: rgba(90,90,90,0.75); }
            QPushButton:checked { background-color: rgba(80,120,80,0.75); border: 1px solid rgba(120,200,120,0.30); }
        """)
        self.overlay_btn.setCheckable(True)
        # --- End overlay toggle ---

        # --- Pop-out on play: overlay up and this window hidden when a run starts here ---
        self.popout_check = QCheckBox("On play: show overlay, hide this window")
        self.popout_check.setChecked(True)
        self.popout_check.setCursor(Qt.PointingHandCursor)
        self.popout_check.setToolTip("Starting a run from this window brings up the overlay and hides "
                                     "this window. Click the Dock icon to bring the window back.")
        self.popout_check.setStyleSheet("""
            QCheckBox { color: rgba(255,255,255,0.70); font-size: 13px; spacing: 8px; background: transparent; }
            QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
                                   border: 1px solid rgba(255,255,255,0.35); background: rgba(255,255,255,0.08); }
            QCheckBox::indicator:checked { background: rgba(120,200,120,0.85); border: 1px solid rgba(120,200,120,0.95); }
        """)
        # --- End pop-out on play ---

        small_slider_css = """
            QSlider { background: transparent; }
            QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.15); border-radius: 2px; }
            QSlider::sub-page:horizontal { background: rgba(255,255,255,0.45); border-radius: 2px; }
            QSlider::handle:horizontal { width: 18px; margin: -7px 0; border-radius: 9px; background: rgba(255,255,255,0.80); }
        """

        # --- Overlay size slider (controls overlay diameter, 80-400 px) ---
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setMinimum(1)
        self.size_slider.setMaximum(100)
        self.size_slider.setValue(25)      # ~160 px default
        self.size_slider.setFixedHeight(22)
        self.size_slider.setMinimumWidth(150)
        self.size_slider.setToolTip("Overlay size")
        self.size_slider.setStyleSheet(small_slider_css)
        # --- End overlay size slider ---

        # --- Overlay opacity slider (controls overlay background transparency) ---
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setMinimum(1)    # min 1% — overlay always draggable
        self.opacity_slider.setMaximum(100)
        self.opacity_slider.setValue(47)      # ~120/255 default
        self.opacity_slider.setFixedHeight(22)
        self.opacity_slider.setMinimumWidth(150)
        self.opacity_slider.setToolTip("Overlay transparency")
        self.opacity_slider.setStyleSheet(small_slider_css)
        # --- End overlay opacity slider ---

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addLayout(top)
        layout.addWidget(self.exercise_combo)
        layout.addStretch(1)
        layout.addWidget(self.circle, alignment=Qt.AlignHCenter)
        layout.addStretch(2)

        # Bottom row: voice volume on the left, labelled overlay controls on the right
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(make_slider_label("Voice"))
        bottom_row.addSpacing(8)
        bottom_row.addWidget(self.volume_slider, stretch=1)
        bottom_row.addSpacing(28)

        overlay_grid = QGridLayout()
        overlay_grid.setHorizontalSpacing(10)
        overlay_grid.setVerticalSpacing(4)
        overlay_grid.addWidget(make_slider_label("Size"), 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        overlay_grid.addWidget(self.size_slider, 0, 1)
        overlay_grid.addWidget(make_slider_label("Transparency"), 1, 0, Qt.AlignRight | Qt.AlignVCenter)
        overlay_grid.addWidget(self.opacity_slider, 1, 1)
        overlay_grid.addWidget(self.overlay_btn, 2, 0, 1, 2)
        overlay_grid.addWidget(self.popout_check, 3, 0, 1, 2, Qt.AlignHCenter)
        bottom_row.addLayout(overlay_grid)

        layout.addLayout(bottom_row)
        layout.addSpacing(12)

# =========================
# MainWindow / Controller
# =========================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Breathe")
        # Only the width is fixed here. With no explicit minimum height, Qt sets
        # the window's minimum height from the tallest screen's layout, so the
        # Exercises screen can never be squeezed (which clipped labels/handles).
        self.setMinimumWidth(920)

        self.voice = Voice()

        # data
        self.settings = load_settings()
        default_cycles_fallback = _sanitize_cycles(self.settings.get("last_cycles", 11), 11)
        self.exercises_data = load_exercises(default_cycles_fallback=default_cycles_fallback)

        saved_ex = self.settings.get("last_exercise")
        if isinstance(saved_ex, str) and saved_ex in self.exercises_data:
            default_name = saved_ex
        else:
            default_name = "4-4-4-4" if "4-4-4-4" in self.exercises_data else sorted(self.exercises_data.keys())[0]

        self.current_exercise = Exercise.from_dict(default_name, self.exercises_data[default_name])

        # IMPORTANT FIX:
        # cycles_total now comes from the selected exercise (per-exercise persistence)
        self.cycles_total = _sanitize_cycles(self.exercises_data[default_name].get("cycles", default_cycles_fallback), default_cycles_fallback)

        # run state machine: "ready" -> "countdown" -> "running" -> "done".
        # A click on the circle (or the overlay) moves ready/done -> countdown
        # and pauses / resumes countdown and running. `paused` applies to both.
        self.state = "ready"
        self.paused = False

        self.countdown_seconds = 3.0
        self.countdown_started_at = 0.0

        self.phase_index = 0
        self.phase_started_at = 0.0
        self.pause_started_at = 0.0
        self.paused_accum = 0.0

        self.cycles_left = 0
        self.phases: List[Tuple[str, float, bool]] = []

        # True while update_exercises_ui_from_current() is pushing an exercise
        # into the sliders, so those programmatic moves are not saved as edits.
        self._syncing_exercise_ui = False

        # Overlay window (created once, shown/hidden as needed). It mirrors the
        # main circle, and a single click on it does exactly what a click on
        # the main circle does: start, pause or resume.
        self.overlay = OverlayWindow()
        self.overlay.set_click_callback(self.on_circle_clicked)

        # Timestamp of the user's last interaction with either overlay.
        # Used to make sure overlay clicks NEVER pop the hidden main
        # window back up (only a real Dock-icon click should).
        self._last_overlay_interaction = 0.0
        # True once macOS Dock-icon clicks are hooked (set at end of __init__).
        self._dock_click_hooked = False

        # UI stack
        self.stack = QStackedWidget()
        self.home = HomeScreen()
        self.exercises = ExercisesScreen()
        self.run = RunScreen()
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.exercises)
        self.stack.addWidget(self.run)
        self.setCentralWidget(self.stack)

        # Wire home
        self.home.btn_breathe.clicked.connect(self.go_run_from_current_selection)
        self.home.btn_exercises.clicked.connect(self.go_exercises)
        self.home.btn_settings.clicked.connect(lambda: self.not_implemented("Settings"))
        self.home.btn_benefits.clicked.connect(lambda: self.not_implemented("Benefits"))
        self.home.btn_more.clicked.connect(lambda: self.not_implemented("More"))

        # Exercises screen
        self.exercises.back_btn.clicked.connect(self.go_home_and_reset_run)
        self.exercises.menu_btn.clicked.connect(self.exercise_menu)
        self.exercises.btn_start.clicked.connect(self.go_run_from_exercises_screen)
        self.exercises.combo.currentTextChanged.connect(self.on_select_exercise_from_exercises_screen)

        for sl in (self.exercises.sl_inhale, self.exercises.sl_hold1, self.exercises.sl_exhale, self.exercises.sl_hold2):
            sl.slider.valueChanged.connect(self.update_length_label)
            sl.slider.valueChanged.connect(self.on_timing_changed)
        self.exercises.cycles_spin.valueChanged.connect(self.update_length_label)
        self.exercises.fib_combo.currentTextChanged.connect(self.update_length_label)
        self.exercises.fib_combo.currentTextChanged.connect(self.on_fib_start_changed)
        self.exercises.fib_rest_spin.valueChanged.connect(self.update_length_label)
        self.exercises.fib_rest_spin.valueChanged.connect(self.on_fib_rest_changed)

        # IMPORTANT FIX:
        # when cycles changes, we save it into that selected exercise (not global settings)
        self.exercises.cycles_spin.valueChanged.connect(self.on_cycles_changed)

        # Run screen
        self.run.back_btn.clicked.connect(self.go_home_and_reset_run)
        self.run.circle.clicked.connect(self.on_main_circle_clicked)
        self.run.exercise_combo.currentTextChanged.connect(self.on_select_exercise_from_run_dropdown)

        # Volume slider (app-level TTS only)
        saved_volume = int(self.settings.get("volume", 100))
        saved_volume = max(0, min(100, saved_volume))
        self.run.volume_slider.setValue(saved_volume)
        self.voice.set_volume(saved_volume)
        self.run.volume_slider.valueChanged.connect(self.on_volume_changed)

        # Overlay toggle button
        self.run.overlay_btn.toggled.connect(self.on_overlay_toggled)

        # Pop-out on play (restore the saved choice, then wire)
        self.run.popout_check.blockSignals(True)
        self.run.popout_check.setChecked(bool(self.settings.get("play_pops_out", True)))
        self.run.popout_check.blockSignals(False)
        self.run.popout_check.toggled.connect(self.on_popout_toggled)

        # Overlay opacity slider (restore saved transparency, then wire)
        saved_opacity = max(1, min(100, int(self.settings.get("overlay_opacity", 47))))
        self.overlay.set_bg_alpha(max(3, int(saved_opacity * 255 / 100)))
        self.run.opacity_slider.blockSignals(True)
        self.run.opacity_slider.setValue(saved_opacity)
        self.run.opacity_slider.blockSignals(False)
        self.run.opacity_slider.valueChanged.connect(self.on_overlay_opacity_changed)

        # Overlay size slider (restore saved size, then wire)
        saved_size = max(80, min(400, int(self.settings.get("overlay_size", OVERLAY_SIZE))))
        self.overlay.set_size(saved_size)
        self.run.size_slider.blockSignals(True)
        self.run.size_slider.setValue(max(1, min(100, round((saved_size - 80) / 3.2))))
        self.run.size_slider.blockSignals(False)
        self.run.size_slider.valueChanged.connect(self.on_overlay_size_changed)

        # Overlay position: back to the saved spot if it is still on a screen,
        # else the default corner. Done after the size so the saved top-left
        # matches the restored size and the corner suits that size.
        saved_x, saved_y = self.settings.get("overlay_x"), self.settings.get("overlay_y")
        restored = (saved_x is not None and saved_y is not None
                    and self.overlay.restore_position(int(saved_x), int(saved_y)))
        if not restored:
            self.overlay.move_to_default_corner()
        self.overlay.set_moved_callback(self.on_overlay_moved)

        # Fill combos
        self.refresh_exercise_combos()

        # animation timer
        self.timer = QTimer(self)
        self.timer.setInterval(33)  # ~30fps
        self.timer.timeout.connect(self.on_tick)
        self.timer.start()

        # start at home
        self.stack.setCurrentWidget(self.home)

        # sync UI with default
        self.update_exercises_ui_from_current()

        # Overlay showing when the app was last quit: come back the same way,
        # straight into the Breathe window (ready state) with the overlay up.
        if self.settings.get("overlay_visible"):
            self.go_run_from_current_selection()
            self.run.overlay_btn.setChecked(True)

        # Keep running in the background when the window is closed (Music-app style)
        app = QApplication.instance()
        app.setQuitOnLastWindowClosed(False)
        app.installEventFilter(self)
        app.aboutToQuit.connect(self._shutdown)

        # Dock-icon clicks are reported by macOS separately from plain
        # activation. Hook them so the main window can always be reopened
        # from the Dock, even while an overlay is showing.
        self._dock_click_hooked = mac_install_dock_click_handler(self._on_dock_icon_clicked)

    def closeEvent(self, event):
        # Music-app behavior: the red close button only hides the window;
        # the session (voice + overlays) keeps running in the background.
        # Click the Dock icon to bring the window back.
        # Really quit with Cmd+Q or right-click the Dock icon -> Quit.
        event.accept()

    def eventFilter(self, watched, event):
        # Record any mouse interaction with the overlays so we can tell
        # overlay clicks apart from real Dock-icon clicks.
        if watched is self.overlay:
            if event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
                QEvent.Type.MouseMove,
                QEvent.Type.Enter,
                QEvent.Type.Leave,
            ):
                self._last_overlay_interaction = time.time()

        # Dock icon clicked / app reactivated -> bring the window back.
        # Deferred check: clicking an overlay can briefly activate the app,
        # and that must NOT pop the hidden main window back up. Only an
        # activation with no recent overlay interaction (i.e. a real Dock
        # click) restores the window.
        if event.type() == QEvent.Type.ApplicationActivate and not self.isVisible():
            QTimer.singleShot(150, self._maybe_restore_window)
        return False

    def _maybe_restore_window(self):
        """Called shortly after the app became active (not from a Dock click)."""
        if self.isVisible():
            return
        overlay_showing = self.overlay.isVisible()
        if overlay_showing and self._dock_click_hooked:
            # An overlay is showing: becoming active (e.g. clicking the
            # overlay after a long pause) must NEVER open the main window.
            # A Dock-icon click still opens it, via _on_dock_icon_clicked.
            return
        # Fallback timing guard (also used when the Dock hook is unavailable,
        # so that a Dock click can still bring the window back).
        if time.time() - self._last_overlay_interaction < 1.0:
            return  # activation came from touching an overlay — stay hidden
        self._restore_window()

    def _restore_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_dock_icon_clicked(self):
        """macOS 'reopen' event: the user clicked the Dock icon.
        Clicking an overlay never sends this, so it is safe to open here."""
        if self.isMinimized():
            self.showNormal()
        elif self.isVisible():
            return
        self._restore_window()

    def _shutdown(self):
        try:
            self._store_overlay_position()  # catch-all: where it was at quit
            save_settings(self.settings)
        except Exception:
            pass
        try:
            self.overlay.close()
        except Exception:
            pass
        try:
            self.voice.shutdown()
        except Exception:
            pass

    # ---------- Overlay ----------
    def on_overlay_toggled(self, checked: bool):
        if checked:
            self.overlay.show()
            self.run.overlay_btn.setText("Hide Overlay")
            self._render_current()  # mirror the current state right away
        else:
            self.overlay.hide()
            self.run.overlay_btn.setText("Show Overlay")
        # Remembered so the next launch comes up the same way
        self.settings["overlay_visible"] = bool(checked)
        save_settings(self.settings)

    def on_overlay_opacity_changed(self, value: int):
        """Map slider 1-100 to alpha 3-255, push to overlay, persist it."""
        alpha = max(3, int(value * 255 / 100))
        self.overlay.set_bg_alpha(alpha)
        self.settings["overlay_opacity"] = int(value)
        save_settings(self.settings)

    def on_overlay_size_changed(self, value: int):
        """Map slider 1-100 to overlay diameter 80-400 px, persist it."""
        px = int(80 + value * 3.2)
        self.overlay.set_size(px)  # keeps the centre, so the top-left moves too
        self.settings["overlay_size"] = px
        self._store_overlay_position()
        save_settings(self.settings)

    def _store_overlay_position(self):
        p = self.overlay.frameGeometry().topLeft()
        self.settings["overlay_x"] = int(p.x())
        self.settings["overlay_y"] = int(p.y())

    def on_overlay_moved(self):
        """A drag ended somewhere new: remember where the overlay is."""
        self._store_overlay_position()
        save_settings(self.settings)

    # ---------- Rendering (main circle + overlay always show the same state) ----------
    def _render(self, center_text: str, progress: float, is_hold: bool,
                sub_text: str = "", show_play: bool = False, overlay_text: str = "",
                seconds_text: str = "", overlay_sub: str = ""):
        """Draw one state on the main circle and mirror it on the overlay."""
        self.run.circle.set_state(center_text, progress, sub_text, is_hold, show_play, seconds_text)
        if self.overlay.isVisible():
            self.overlay.set_state(progress, is_hold, overlay_text, show_play, overlay_sub)

    def _run_length_text(self) -> str:
        n = max(1, int(self.cycles_total))
        return f"{n} sets" if self.current_exercise.is_fibonacci else f"{n} cycles"

    def _render_ready(self):
        self._render("", 0.0, False, sub_text=self._run_length_text(), show_play=True)

    def _render_done(self):
        self._render("", 0.0, False, sub_text="Done", show_play=True)

    def _render_countdown(self):
        elapsed = max(0.0, min(self.countdown_seconds, self._countdown_elapsed()))
        digit = str(max(1, int(math.ceil(self.countdown_seconds - elapsed))))
        # Full ring + bar filling over the countdown, so a paused countdown
        # visibly stands still (no text or icon needed).
        self._render(digit, elapsed / self.countdown_seconds, True, overlay_text=digit)

    def _render_running(self):
        phase_name, phase_seconds, is_hold = self.phases[self.phase_index]
        raw = self._phase_elapsed() / float(phase_seconds) if phase_seconds > 0 else 1.0
        raw = max(0.0, min(1.0, raw))
        # Inhale: 0->1, Exhale: 1->0 visually
        draw_progress = 1.0 - raw if phase_name.lower() == "exhale" else raw
        if self.current_exercise.is_fibonacci:
            cycles_text = f"{self.cycles_left} sets left"
            # Fibonacci: the length of this breath (13, 8, 5 ...) is the number
            # that matters, so it goes under the phase word in the window and
            # becomes the big number on the overlay, with sets left small under it.
            seconds_text = "" if is_hold else f"{float(phase_seconds):g}"
        else:
            cycles_text = f"{self.cycles_left} cycles left"
            seconds_text = ""
        if seconds_text:
            self._render(phase_name, draw_progress, is_hold, sub_text=cycles_text,
                         overlay_text=seconds_text, seconds_text=seconds_text,
                         overlay_sub=str(self.cycles_left))
        else:
            self._render(phase_name, draw_progress, is_hold,
                         sub_text=cycles_text, overlay_text=str(self.cycles_left))

    def _render_current(self):
        """Re-draw whatever state we are in (used when the overlay is shown)."""
        if self.state == "countdown":
            self._render_countdown()
        elif self.state == "running":
            self._render_running()
        elif self.state == "done":
            self._render_done()
        else:
            self._render_ready()

    # ---------- Clock (pauses freeze it) ----------
    def _now(self) -> float:
        # While paused, time stands still at the moment the pause began.
        return self.pause_started_at if self.paused else time.time()

    def _countdown_elapsed(self) -> float:
        return (self._now() - self.countdown_started_at) - self.paused_accum

    def _phase_elapsed(self) -> float:
        return (self._now() - self.phase_started_at) - self.paused_accum

    # ---------- Circle click (main circle and overlay) ----------
    def on_circle_clicked(self):
        """Ready / Done: start (countdown, then breathing).
        Countdown / Running: pause; click again to resume from the same spot."""
        if self.state in ("ready", "done"):
            self.start_run_fresh()
        else:
            self.toggle_pause()

    def on_main_circle_clicked(self):
        """A click on the big circle in this window. Same as on_circle_clicked,
        plus the pop-out when it starts a run: overlay up, this window hidden
        (the Dock icon brings it back). Clicks on the overlay never hide it."""
        starting = self.state in ("ready", "done")
        self.on_circle_clicked()
        if starting and self.run.popout_check.isChecked():
            self.pop_out_to_overlay()

    def pop_out_to_overlay(self):
        if not self.run.overlay_btn.isChecked():
            self.run.overlay_btn.setChecked(True)  # shows the overlay and remembers it
        self.hide()  # like the red close button: the session keeps running

    def on_popout_toggled(self, checked: bool):
        self.settings["play_pops_out"] = bool(checked)
        save_settings(self.settings)

    # ---------- Navigation ----------
    def go_home_and_reset_run(self):
        self.stop_run(reset_visual=True)
        # Hide overlays when leaving run screen
        self.overlay.hide()
        self.run.overlay_btn.setChecked(False)
        self.run.overlay_btn.setText("Show Overlay")
        self.stack.setCurrentWidget(self.home)

    def go_exercises(self):
        self.stop_run(reset_visual=False)  # don't keep running in background
        self.stack.setCurrentWidget(self.exercises)

    def go_run(self):
        self.stack.setCurrentWidget(self.run)

    def not_implemented(self, name: str):
        QMessageBox.information(self, name, f"{name} screen is not implemented yet.\n\nWe can add it next.")

    def persist_last_state(self):
        # Keep last selected exercise for restoring.
        if not hasattr(self, "settings") or not isinstance(self.settings, dict):
            self.settings = {"last_exercise": None, "last_cycles": 11}

        self.settings["last_exercise"] = self.current_exercise.name if self.current_exercise else None

        # Keep writing last_cycles too (backward compatible fallback),
        # but cycles persistence is per-exercise now.
        self.settings["last_cycles"] = int(self.cycles_total) if self.cycles_total else 11

        save_settings(self.settings)

    def on_cycles_changed(self, value: int):
        # IMPORTANT FIX: persist cycles per-exercise
        v = _sanitize_cycles(value, 11)
        self.cycles_total = v

        name = self.current_exercise.name if self.current_exercise else ""
        if name and name in self.exercises_data:
            self.exercises_data[name]["cycles"] = v
            save_exercises(self.exercises_data)

        # Also keep last_exercise updated (and last_cycles as fallback)
        self.persist_last_state()

    def on_fib_start_changed(self, text: str):
        """Persist the starting Fibonacci number per exercise (like cycles)."""
        v = _sanitize_fib_start(text)
        if self.current_exercise:
            self.current_exercise.fib_start = v
            name = self.current_exercise.name
            if name in self.exercises_data:
                self.exercises_data[name]["fib_start"] = v
                save_exercises(self.exercises_data)

    def on_fib_rest_changed(self, value: int):
        """Persist the rest between Fibonacci sets per exercise (like cycles)."""
        v = _sanitize_fib_rest(value)
        if self.current_exercise:
            self.current_exercise.fib_rest = v
            name = self.current_exercise.name
            if name in self.exercises_data:
                self.exercises_data[name]["fib_rest"] = v
                save_exercises(self.exercises_data)

    def on_timing_changed(self, _value: int = 0):
        """Persist the Inhale / Hold / Exhale / Hold sliders per exercise the
        moment they move (like cycles and the Fibonacci settings), and keep
        the current exercise in step so the next run uses them."""
        if self._syncing_exercise_ui or not self.current_exercise:
            return
        ex = self.current_exercise
        ex.inhale = round(self.exercises.sl_inhale.value(), 1)
        ex.hold1 = round(self.exercises.sl_hold1.value(), 1)
        ex.exhale = round(self.exercises.sl_exhale.value(), 1)
        ex.hold2 = round(self.exercises.sl_hold2.value(), 1)
        name = ex.name
        if name in self.exercises_data:
            d = self.exercises_data[name]
            d["inhale"], d["hold1"], d["exhale"], d["hold2"] = ex.inhale, ex.hold1, ex.exhale, ex.hold2
            save_exercises(self.exercises_data)

    def on_volume_changed(self, value: int):
        """Adjust app-level TTS volume (0-100). Does NOT change system volume."""
        value = max(0, min(100, value))
        self.voice.set_volume(value)
        # persist
        self.settings["volume"] = value
        save_settings(self.settings)

    # ---------- Exercise management ----------
    def refresh_exercise_combos(self):
        names = sorted(self.exercises_data.keys(), key=lambda s: s.lower())

        # exercises screen combo
        self.exercises.combo.blockSignals(True)
        self.exercises.combo.clear()
        self.exercises.combo.addItems(names)
        self.exercises.combo.setCurrentText(self.current_exercise.name)
        self.exercises.combo.blockSignals(False)

        # run screen dropdown
        self.run.exercise_combo.blockSignals(True)
        self.run.exercise_combo.clear()
        self.run.exercise_combo.addItems(names)
        self.run.exercise_combo.setCurrentText(self.current_exercise.name)
        self.run.exercise_combo.blockSignals(False)

    def update_exercises_ui_from_current(self):
        ex = self.current_exercise
        self._syncing_exercise_ui = True
        try:
            self.exercises.sl_inhale.setValue(ex.inhale)
            self.exercises.sl_hold1.setValue(ex.hold1)
            self.exercises.sl_exhale.setValue(ex.exhale)
            self.exercises.sl_hold2.setValue(ex.hold2)
        finally:
            self._syncing_exercise_ui = False

        self.exercises.fib_combo.blockSignals(True)
        self.exercises.fib_combo.setCurrentText(str(ex.fib_start))
        self.exercises.fib_combo.blockSignals(False)
        self.exercises.fib_rest_spin.blockSignals(True)
        self.exercises.fib_rest_spin.setValue(ex.fib_rest)
        self.exercises.fib_rest_spin.blockSignals(False)
        self.exercises.set_fibonacci_mode(ex.is_fibonacci)

        # IMPORTANT FIX: set spinbox from the selected exercise cycles
        self.exercises.cycles_spin.blockSignals(True)
        self.exercises.cycles_spin.setValue(self.cycles_total)
        self.exercises.cycles_spin.blockSignals(False)

        self.update_length_label()

    def update_length_label(self):
        cycles = int(self.exercises.cycles_spin.value())
        if self.current_exercise and self.current_exercise.is_fibonacci:
            seq = fibonacci_descending(self.exercises.fib_combo.currentText())
            rest = _sanitize_fib_rest(self.exercises.fib_rest_spin.value())
            per_set = float(sum(2 * n for n in seq))
            steps = ", ".join(f"{n} in / {n} out" for n in seq)
            text = f"One set: {steps}"
            if rest > 0:
                text += f"\nThen {rest} seconds of normal breathing before the next set"
            self.exercises.fib_sequence_label.setText(text)
            # The rest happens between sets only, so it occurs (sets - 1) times.
            total = per_set * cycles + rest * max(0, cycles - 1)
        else:
            inhale = self.exercises.sl_inhale.value()
            hold1 = self.exercises.sl_hold1.value()
            exhale = self.exercises.sl_exhale.value()
            hold2 = self.exercises.sl_hold2.value()
            per_cycle = inhale + hold1 + exhale + hold2
            total = per_cycle * cycles
        mins = int(total) // 60
        secs = total - (mins * 60)
        self.exercises.length_label.setText(f"Exercise Length: {mins} minutes, {secs:.1f} seconds")

    def on_select_exercise_from_exercises_screen(self, name: str):
        if not name:
            return
        d = self.exercises_data.get(name)
        if not d:
            return

        self.current_exercise = Exercise.from_dict(name, d)

        # IMPORTANT FIX: load cycles from this specific exercise
        self.cycles_total = _sanitize_cycles(d.get("cycles", 11), 11)

        self.persist_last_state()
        # keep both dropdowns synced
        self.refresh_exercise_combos()
        self.update_exercises_ui_from_current()

    def on_select_exercise_from_run_dropdown(self, name: str):
        if not name:
            return
        d = self.exercises_data.get(name)
        if not d:
            return

        self.current_exercise = Exercise.from_dict(name, d)

        # IMPORTANT FIX: load cycles from this specific exercise
        self.cycles_total = _sanitize_cycles(d.get("cycles", 11), 11)

        self.persist_last_state()
        # sync exercises screen combo too
        self.refresh_exercise_combos()
        self.update_exercises_ui_from_current()
        # Back to the ready state for the new exercise (click the circle to start)
        if self.stack.currentWidget() == self.run:
            self.prepare_run()

    def exercise_menu(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Exercises")
        dlg.setText("What do you want to do?")
        btn_add = dlg.addButton("Add new", QMessageBox.ActionRole)
        btn_save = dlg.addButton("Save changes to this", QMessageBox.ActionRole)
        btn_rename = dlg.addButton("Rename this", QMessageBox.ActionRole)
        btn_delete = dlg.addButton("Delete this", QMessageBox.DestructiveRole)
        dlg.addButton("Cancel", QMessageBox.RejectRole)
        dlg.exec()

        clicked = dlg.clickedButton()
        current_name = self.exercises.combo.currentText()

        if clicked == btn_add:
            self.add_exercise()
        elif clicked == btn_save:
            self.save_current_changes(current_name)
        elif clicked == btn_rename:
            self.rename_exercise(current_name)
        elif clicked == btn_delete:
            self.delete_exercise(current_name)

    def rename_exercise(self, old_name: str):
        if not old_name:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Rename Exercise")
        dlg.setModal(True)
        dlg.setStyleSheet("background-color: #0b0b0b; color: white;")
        dlg.resize(380, 140)

        name_edit = QLineEdit(old_name)
        name_edit.setStyleSheet("padding: 8px; font-size: 16px;")
        name_edit.selectAll()

        btn_ok = QPushButton("Rename")
        btn_cancel = QPushButton("Cancel")
        for b in (btn_ok, btn_cancel):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { padding: 10px; border-radius: 10px; background: rgba(255,255,255,0.12); color: white; font-size: 16px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.20); }"
            )
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)

        form = QFormLayout()
        form.addRow("New name:", name_edit)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)

        layout = QVBoxLayout(dlg)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return

        new_name = name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Error", "Exercise name cannot be empty.")
            return
        if new_name == old_name:
            return
        if new_name in self.exercises_data:
            QMessageBox.warning(self, "Error", f"An exercise named '{new_name}' already exists.")
            return

        # Move data from old key to new key
        self.exercises_data[new_name] = self.exercises_data.pop(old_name)
        save_exercises(self.exercises_data)

        # Update current exercise reference
        self.current_exercise = Exercise.from_dict(new_name, self.exercises_data[new_name])
        self.cycles_total = _sanitize_cycles(self.exercises_data[new_name].get("cycles", 11), 11)

        self.persist_last_state()
        self.refresh_exercise_combos()
        self.update_exercises_ui_from_current()
        QMessageBox.information(self, "Renamed", f"Renamed '{old_name}' → '{new_name}'.")

    def add_exercise(self):
        dlg = ExerciseDialog(self)
        if dlg.exec() == QDialog.Accepted:
            ex = dlg.get_exercise()
            if not ex.name:
                QMessageBox.warning(self, "Error", "Exercise name cannot be empty.")
                return

            # IMPORTANT FIX: new exercise also gets a cycles field
            default_cycles = _sanitize_cycles(self.exercises.cycles_spin.value(), 11)
            ex.cycles = default_cycles
            self.exercises_data[ex.name] = ex.to_dict()
            save_exercises(self.exercises_data)

            self.current_exercise = Exercise.from_dict(ex.name, self.exercises_data[ex.name])
            self.cycles_total = default_cycles

            self.refresh_exercise_combos()
            self.update_exercises_ui_from_current()

    def save_current_changes(self, name: str):
        if not name:
            return

        # IMPORTANT FIX: save cycles together with the exercise
        cycles = _sanitize_cycles(self.exercises.cycles_spin.value(), 11)

        existing = self.exercises_data.get(name, {})
        self.exercises_data[name] = {
            "mode": existing.get("mode", "normal"),
            "fib_start": _sanitize_fib_start(self.exercises.fib_combo.currentText()),
            "fib_rest": _sanitize_fib_rest(self.exercises.fib_rest_spin.value()),
            "inhale": round(self.exercises.sl_inhale.value(), 1),
            "hold1": round(self.exercises.sl_hold1.value(), 1),
            "exhale": round(self.exercises.sl_exhale.value(), 1),
            "hold2": round(self.exercises.sl_hold2.value(), 1),
            "cycles": cycles,
        }
        save_exercises(self.exercises_data)

        # update current exercise + cycles_total
        self.current_exercise = Exercise.from_dict(name, self.exercises_data[name])
        self.cycles_total = cycles

        self.persist_last_state()
        self.refresh_exercise_combos()
        QMessageBox.information(self, "Saved", f"Saved changes to '{name}'.")

    def delete_exercise(self, name: str):
        if not name:
            return
        if len(self.exercises_data) <= 1:
            QMessageBox.warning(self, "Cannot delete", "You must keep at least one exercise.")
            return
        ok = QMessageBox.question(self, "Delete", f"Delete '{name}'?", QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self.exercises_data.pop(name, None)
        save_exercises(self.exercises_data)
        # choose a new current
        new_name = sorted(self.exercises_data.keys())[0]
        self.current_exercise = Exercise.from_dict(new_name, self.exercises_data[new_name])
        self.cycles_total = _sanitize_cycles(self.exercises_data[new_name].get("cycles", 11), 11)
        self.persist_last_state()
        self.refresh_exercise_combos()
        self.update_exercises_ui_from_current()

    # ---------- Run logic ----------
    def build_phases(self, ex: Exercise) -> List[Tuple[str, float, bool]]:
        phases: List[Tuple[str, float, bool]] = []
        if ex.is_fibonacci:
            # Optional rest (normal breathing) before a set. It leads every set's
            # phase list, and the run skips it for the very first set, so it only
            # ever happens between two sets.
            if ex.fib_rest > 0:
                phases.append(("Rest", float(ex.fib_rest), True))
            # One set = descending Fibonacci breaths, each N seconds in and N seconds out.
            for n in fibonacci_descending(ex.fib_start):
                phases.append(("Inhale", float(n), False))
                phases.append(("Exhale", float(n), False))
            return phases
        if ex.inhale > 0:
            phases.append(("Inhale", ex.inhale, False))
        if ex.hold1 > 0:
            phases.append(("Hold", ex.hold1, True))
        if ex.exhale > 0:
            phases.append(("Exhale", ex.exhale, False))
        if ex.hold2 > 0:
            phases.append(("Hold", ex.hold2, True))
        if not phases:
            phases = [("Hold", 1.0, True)]
        return phases

    def _first_phase_index(self) -> int:
        """The first set starts breathing right away: skip a leading Rest phase."""
        if len(self.phases) > 1 and self.phases[0][0] == "Rest":
            return 1
        return 0

    def stop_run(self, reset_visual: bool):
        self.state = "ready"
        self.paused = False
        self.phase_index = 0
        self.paused_accum = 0.0
        self.countdown_started_at = 0.0
        if reset_visual:
            self._render_ready()

    def prepare_run(self):
        """Enter the ready state: phases built, play triangle showing, and
        nothing running until the circle (or the overlay) is clicked."""
        self.stop_run(reset_visual=False)
        self.phases = self.build_phases(self.current_exercise)
        self.phase_index = self._first_phase_index()
        self.cycles_left = max(1, self.cycles_total)
        self._render_ready()

    def start_run_fresh(self):
        """Start the 3-2-1 countdown; breathing begins when it ends."""
        self.prepare_run()
        self.state = "countdown"
        self.paused = False
        self.paused_accum = 0.0
        self.countdown_started_at = time.time()
        self._render_countdown()

    def _begin_breathing_now(self):
        self.state = "running"
        self.paused = False
        self.paused_accum = 0.0
        self.phase_started_at = time.time()
        # speak first phase
        self.voice.say(self.phases[self.phase_index][0])

    def toggle_pause(self):
        """Pause or resume the countdown or the breathing. No text or icon:
        the arc (or the countdown bar) standing still is the feedback."""
        if self.state not in ("countdown", "running"):
            return
        self.paused = not self.paused
        if self.paused:
            self.pause_started_at = time.time()
        else:
            # accumulate pause duration so elapsed excludes it
            self.paused_accum += (time.time() - self.pause_started_at)

    def advance_phase(self):
        self.phase_index += 1
        if self.phase_index >= len(self.phases):
            self.phase_index = 0
            self.cycles_left -= 1
            if self.cycles_left <= 0:
                self.state = "done"
                self.paused = False
                self.voice.say("Done")
                self._render_done()  # play triangle again: click to go once more
                return
        self.phase_started_at = time.time()
        self.paused_accum = 0.0
        self.voice.say(self.phases[self.phase_index][0])

    # ---------- Button actions ----------
    def go_run_from_current_selection(self):
        # Use current spinbox value (and it is already per-exercise persisted via on_cycles_changed)
        self.cycles_total = _sanitize_cycles(self.exercises.cycles_spin.value(), 11)

        # ensure exercises_data has latest cycles
        name = self.current_exercise.name
        if name in self.exercises_data:
            self.exercises_data[name]["cycles"] = self.cycles_total
            save_exercises(self.exercises_data)

        self.persist_last_state()
        self.refresh_exercise_combos()
        self.go_run()
        self.prepare_run()

    def go_run_from_exercises_screen(self):
        # pull slider values into current exercise
        self.current_exercise.inhale = round(self.exercises.sl_inhale.value(), 1)
        self.current_exercise.hold1 = round(self.exercises.sl_hold1.value(), 1)
        self.current_exercise.exhale = round(self.exercises.sl_exhale.value(), 1)
        self.current_exercise.hold2 = round(self.exercises.sl_hold2.value(), 1)
        self.current_exercise.fib_start = _sanitize_fib_start(self.exercises.fib_combo.currentText())
        self.current_exercise.fib_rest = _sanitize_fib_rest(self.exercises.fib_rest_spin.value())

        self.cycles_total = _sanitize_cycles(self.exercises.cycles_spin.value(), 11)

        # Timings, Fibonacci settings and cycles are all persisted per exercise the
        # moment they change (on_timing_changed / on_fib_* / on_cycles_changed).
        # This only makes sure the cycles on disk match what this run uses.
        name = self.current_exercise.name
        if name in self.exercises_data:
            self.exercises_data[name]["cycles"] = self.cycles_total
            save_exercises(self.exercises_data)

        self.persist_last_state()
        self.refresh_exercise_combos()
        self.go_run()
        self.prepare_run()

    # ---------- Tick ----------
    def on_tick(self):
        if self.stack.currentWidget() != self.run:
            return
        if self.paused:
            return  # nothing moves: the still arc / bar is the pause feedback

        if self.state == "countdown":
            if self._countdown_elapsed() >= self.countdown_seconds:
                self._begin_breathing_now()
            else:
                self._render_countdown()
            return

        if self.state != "running":
            return

        phase_seconds = self.phases[self.phase_index][1]
        if phase_seconds <= 0 or self._phase_elapsed() >= float(phase_seconds):
            self.advance_phase()
            return
        self._render_running()

# =========================
# Main
# =========================

_SINGLE_LOCK = None  # held for the app lifetime

def main():
    global _SINGLE_LOCK
    _SINGLE_LOCK = open(os.path.join(app_data_dir(), "app.lock"), "w")
    try:
        fcntl.flock(_SINGLE_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)  # another copy is already running

    app = QApplication(sys.argv)
    app.setApplicationName("Breathe")
    app.setStyle("Fusion")
    icon_png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    if os.path.exists(icon_png):
        app.setWindowIcon(QIcon(icon_png))  # Dock icon while running
    mac_disable_app_nap()
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()