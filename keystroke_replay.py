#!/usr/bin/env python3
"""
KeystrokeReplay - High-precision input recorder and replayer.

Designed for rhythm games (osu!, Geometry Dash, etc.) where timing accuracy
is critical. Records keyboard, mouse clicks, mouse movement, and scroll events
to JSON, then replays them with sub-millisecond timing.

Global hotkeys (active even when the window is not focused):
  Alt + Shift + R  — Toggle recording on/off
  Alt + Shift + P  — Start playback of last recording
  Escape           — Stop an active recording or playback
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pynput import keyboard, mouse
from pynput.keyboard import HotKey, Key, KeyCode
from pynput.mouse import Button


# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_RECORDING_PATH = Path("recording.json")
VERSION = 1


# ── Helpers ──────────────────────────────────────────────────────────────────

def _key_to_str(key: Key | KeyCode) -> str:
    """Serialize a pynput key to a JSON-safe string."""
    if isinstance(key, KeyCode):
        if key.char is not None:
            return key.char
        # vk-only key (no char representation)
        return f"<vk:{key.vk}>"
    # Special key (Key enum member)
    return key.name


def _str_to_key(s: str) -> Key | KeyCode:
    """Deserialize a string back to a pynput key."""
    if s.startswith("<vk:"):
        vk = int(s[4:-1])
        return KeyCode(vk=vk)
    if len(s) == 1:
        return KeyCode.from_char(s)
    try:
        return Key[s]
    except KeyError:
        return KeyCode.from_char(s)


def _button_to_str(button: Button) -> str:
    return button.name


def _str_to_button(s: str) -> Button:
    return Button[s]


# ── Recorder ─────────────────────────────────────────────────────────────────

class Recorder:
    """Captures all input events with high-resolution timestamps.

    Records keyboard presses/releases, mouse clicks, mouse movement,
    and scroll wheel events with microsecond precision timestamps.
    """

    def __init__(self):
        """Initialize the recorder with empty event list and default state."""
        self._events: list[dict] = []
        self._start: float = 0.0
        self._lock = threading.Lock()
        self._kb_listener: keyboard.Listener | None = None
        self._ms_listener: mouse.Listener | None = None
        self.is_recording = False
        self._recording_lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._recording_lock:
            if self.is_recording:
                return
            self._events = []
            self._start = time.perf_counter()
            self.is_recording = True

        self._kb_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._ms_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
        )
        self._kb_listener.start()
        self._ms_listener.start()
        print("[REC] Recording started — press Alt+Shift+R or Esc to stop.")

    def stop(self) -> list[dict]:
        with self._recording_lock:
            if not self.is_recording:
                return self._events
            self.is_recording = False
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener.join()
        if self._ms_listener:
            self._ms_listener.stop()
            self._ms_listener.join()
        print(f"[REC] Recording stopped — {len(self._events)} events captured.")
        return self._events

    def save(self, path: Path = DEFAULT_RECORDING_PATH) -> None:
        """Persist the last recording to *path* as JSON."""
        if not self._events:
            print("[REC] Nothing to save.")
            return
        duration = self._events[-1]["time"]
        payload = {
            "version": VERSION,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "duration": round(duration, 6),
            "events": self._events,
        }
        path = Path(path)
        # Create parent directory if it doesn't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"[REC] Saved {len(self._events)} events → {path}")
        except (OSError, IOError) as e:
            print(f"[ERROR] Failed to save recording to {path}: {e}")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ts(self) -> float:
        """Elapsed seconds since recording started, 6 decimal places."""
        return round(time.perf_counter() - self._start, 6)

    def _record(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)

    # ── Keyboard callbacks ────────────────────────────────────────────────────

    def _on_key_press(self, key) -> None:
        try:
            self._record({"type": "key_press", "time": self._ts(), "key": _key_to_str(key)})
        except Exception as e:
            print(f"[ERROR] Failed to record key press: {e}")

    def _on_key_release(self, key) -> None:
        try:
            self._record({"type": "key_release", "time": self._ts(), "key": _key_to_str(key)})
        except Exception as e:
            print(f"[ERROR] Failed to record key release: {e}")

    # ── Mouse callbacks ───────────────────────────────────────────────────────

    def _on_mouse_move(self, x: int, y: int) -> None:
        try:
            self._record({"type": "mouse_move", "time": self._ts(), "x": x, "y": y})
        except Exception as e:
            print(f"[ERROR] Failed to record mouse move: {e}")

    def _on_mouse_click(self, x: int, y: int, button: Button, pressed: bool) -> None:
        try:
            self._record({
                "type": "mouse_click",
                "time": self._ts(),
                "x": x,
                "y": y,
                "button": _button_to_str(button),
                "pressed": pressed,
            })
        except Exception as e:
            print(f"[ERROR] Failed to record mouse click: {e}")

    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        try:
            self._record({
                "type": "mouse_scroll",
                "time": self._ts(),
                "x": x,
                "y": y,
                "dx": dx,
                "dy": dy,
            })
        except Exception as e:
            print(f"[ERROR] Failed to record mouse scroll: {e}")


# ── Replayer ──────────────────────────────────────────────────────────────────

class Replayer:
    """Replays a recorded event list with high-precision timing.

    Uses a busy-wait (spin-sleep hybrid) in the final microseconds to
    compensate for OS scheduler jitter — important for rhythm games.
    """

    SPIN_THRESHOLD = 0.001  # spin for the last 1 ms for precision

    def __init__(self):
        """Initialize the replayer with keyboard/mouse controllers and threading state."""
        self._kb_ctrl = keyboard.Controller()
        self._ms_ctrl = mouse.Controller()
        self.is_replaying = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._replay_lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, events: list[dict]) -> None:
        with self._replay_lock:
            if self.is_replaying:
                print("[PLAY] Already replaying.")
                return
            if not events:
                print("[PLAY] No events to replay.")
                return
            # Stop any existing thread before starting a new one
            if self._thread and self._thread.is_alive():
                self._stop_event.set()
                self._thread.join(timeout=2)
            self._stop_event.clear()
            self.is_replaying = True
        self._thread = threading.Thread(
            target=self._run,
            args=(events,),
            daemon=True,
            name="replayer",
        )
        self._thread.start()
        print(f"[PLAY] Playback started — {len(events)} events over "
              f"{events[-1]['time']:.3f}s. Press Esc to stop.")

    def stop(self) -> None:
        with self._replay_lock:
            if not self.is_replaying:
                return
            self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self._replay_lock:
            self.is_replaying = False
        print("[PLAY] Playback stopped.")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _precise_sleep(self, target_time: float, origin: float) -> None:
        """Sleep until *origin + target_time* with busy-wait for the tail.

        Uses a hybrid approach: sleeps for most of the duration, then spins
        for the final millisecond to achieve sub-millisecond precision.
        Includes a timeout safeguard to prevent infinite spinning.
        """
        deadline = origin + target_time
        remaining = deadline - time.perf_counter()
        if remaining > self.SPIN_THRESHOLD:
            time.sleep(remaining - self.SPIN_THRESHOLD)
        # Busy-wait for the last millisecond with timeout safeguard
        max_spins = 1000000  # Safety limit to prevent infinite loop
        spins = 0
        while time.perf_counter() < deadline and spins < max_spins:
            spins += 1
            pass

    def _run(self, events: list[dict]) -> None:
        origin = time.perf_counter()
        try:
            for event in events:
                if self._stop_event.is_set():
                    break
                self._precise_sleep(event["time"], origin)
                if self._stop_event.is_set():
                    break
                self._dispatch(event)
        finally:
            with self._replay_lock:
                self.is_replaying = False

    def _dispatch(self, event: dict) -> None:
        """Execute a single recorded event."""
        try:
            etype = event.get("type")
            if not etype:
                print(f"[WARN] Event missing 'type' field: {event}")
                return

            if etype == "key_press":
                key = _str_to_key(event["key"])
                self._kb_ctrl.press(key)

            elif etype == "key_release":
                key = _str_to_key(event["key"])
                self._kb_ctrl.release(key)

            elif etype == "mouse_move":
                self._ms_ctrl.position = (event["x"], event["y"])

            elif etype == "mouse_click":
                button = _str_to_button(event["button"])
                if event["pressed"]:
                    self._ms_ctrl.press(button)
                else:
                    self._ms_ctrl.release(button)

            elif etype == "mouse_scroll":
                self._ms_ctrl.scroll(event["dx"], event["dy"])

            else:
                print(f"[WARN] Unknown event type '{etype}', skipping event: {event}")

        except KeyError as e:
            print(f"[ERROR] Event missing required field {e}: {event}")
        except Exception as e:
            print(f"[ERROR] Failed to dispatch event: {e}, event: {event}")


# ── Session manager ───────────────────────────────────────────────────────────

class Session:
    """Ties together Recorder, Replayer, and global hotkeys.

    Manages the interactive session with hotkey bindings for recording
    and playback control.
    """

    def __init__(self, recording_path: Path = DEFAULT_RECORDING_PATH):
        """Initialize the session with the specified recording path.

        Args:
            recording_path: Path where recordings will be saved/loaded.
        """
        self._path = recording_path
        self._recorder = Recorder()
        self._replayer = Replayer()
        self._events: list[dict] = []
        self._hotkey_listener: keyboard.Listener | None = None

    # ── Global hotkeys ────────────────────────────────────────────────────────

    def _stop_and_save_recording(self) -> None:
        """Helper method to stop recording and save to disk."""
        self._events = self._recorder.stop()
        self._recorder.save(self._path)

    def _on_hotkey_record(self) -> None:
        if self._recorder.is_recording:
            self._stop_and_save_recording()
        else:
            # Stop any active playback before starting a fresh recording
            if self._replayer.is_replaying:
                self._replayer.stop()
            self._recorder.start()

    def _on_hotkey_play(self) -> None:
        if self._replayer.is_replaying:
            self._replayer.stop()
            return
        # Load from disk if we don't have in-memory events yet
        events = self._events or _load_events(self._path)
        if not events:
            print("[PLAY] No recording found. Record something first (Alt+Shift+R).")
            return
        self._replayer.start(events)

    def _on_hotkey_stop(self) -> None:
        stopped_something = False
        if self._recorder.is_recording:
            self._stop_and_save_recording()
            stopped_something = True
        if self._replayer.is_replaying:
            self._replayer.stop()
            stopped_something = True
        if not stopped_something:
            print("[ESC] Nothing to stop.")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Block until the user exits (Ctrl+C or Ctrl+Q)."""
        print("━━━ KeystrokeReplay ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Alt + Shift + R  →  toggle recording")
        print("  Alt + Shift + P  →  start/stop playback")
        print("  Escape           →  stop recording or playback")
        print("  Ctrl + C         →  quit")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        hotkeys = {
            "<alt>+<shift>+r": self._on_hotkey_record,
            "<alt>+<shift>+p": self._on_hotkey_play,
            "<esc>": self._on_hotkey_stop,
        }

        with keyboard.GlobalHotKeys(hotkeys) as listener:
            try:
                listener.join()
            except KeyboardInterrupt:
                pass
            finally:
                # Clean up any running recorder / replayer
                if self._recorder.is_recording:
                    self._stop_and_save_recording()
                if self._replayer.is_replaying:
                    self._replayer.stop()
                print("\n[QUIT] Goodbye.")


# ── JSON I/O ─────────────────────────────────────────────────────────────────

def _load_events(path: str | Path) -> list[dict]:
    """Load events from a JSON recording file."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        events = data.get("events", [])
        print(f"[LOAD] Loaded {len(events)} events from {path}")
        return events
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Failed to load {path}: {exc}")
        return []


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="KeystrokeReplay — record and replay keyboard/mouse input.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-f", "--file",
        metavar="PATH",
        default=str(DEFAULT_RECORDING_PATH),
        help=f"JSON file for saving/loading recordings (default: {DEFAULT_RECORDING_PATH})",
    )
    parser.add_argument(
        "--play",
        metavar="PATH",
        help="Immediately replay a recording from PATH and exit.",
    )
    args = parser.parse_args()

    if args.play:
        # Non-interactive one-shot replay mode
        events = _load_events(Path(args.play))
        if not events:
            sys.exit(1)
        replayer = Replayer()
        done = threading.Event()

        def _wait_for_done():
            while replayer.is_replaying:
                time.sleep(0.05)
            done.set()

        replayer.start(events)
        watcher = threading.Thread(target=_wait_for_done, daemon=True)
        watcher.start()
        try:
            done.wait()
        except KeyboardInterrupt:
            replayer.stop()
        return

    # Interactive mode — run the full session with hotkeys
    session = Session(recording_path=Path(args.file))
    session.run()


if __name__ == "__main__":
    main()
