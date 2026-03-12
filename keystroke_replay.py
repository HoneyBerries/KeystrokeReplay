#!/usr/bin/env python3
"""
KeystrokeReplay - High-precision input recorder and replayer.

Designed for rhythm games (osu!, Geometry Dash, etc.) where timing accuracy
is critical. Records keyboard, mouse clicks, mouse movement, and scroll events
to JSON, then replays them with sub-millisecond timing.

Interactive menu controls:
  1  —  Start a new recording
  2  —  Play back a saved recording
  3  —  List saved recordings
  4  —  Quit

During recording:
  Alt + Shift + R  —  Stop recording
  Escape           —  Stop recording

During playback:
  Escape           —  Stop playback early
"""

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from colorama import Fore, Style, init as _colorama_init
from pynput import keyboard, mouse
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

_colorama_init(autoreset=True)


# ── Colour helper ────────────────────────────────────────────────────────────

def _c(text: str, *styles: str) -> str:
    """Wrap *text* with colorama style codes followed by a hard reset."""
    return "".join(styles) + str(text) + Style.RESET_ALL


# ── Constants ────────────────────────────────────────────────────────────────

# Recordings are stored under a dedicated folder next to this script so that
# multiple recordings accumulate and are easy to find.
RECORDINGS_DIR = Path(__file__).parent / "recordings"

# Kept for the non-interactive --play CLI mode only.
DEFAULT_RECORDING_PATH = Path("recording.json")

VERSION = 1


# ── Helpers ──────────────────────────────────────────────────────────────────

def _key_to_str(key) -> str:
    """Serialize a pynput key to a JSON-safe string."""
    if isinstance(key, KeyCode):
        if key.char is not None:
            return key.char
        # vk-only key (no char representation)
        return f"<vk:{key.vk}>"
    # Special key (Key enum member)
    return key.name


def _str_to_key(s: str):
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
    """Captures all input events with high-resolution timestamps."""

    def __init__(self):
        self._events: list[dict] = []
        self._start: float = 0.0
        self._lock = threading.Lock()
        self._kb_listener: keyboard.Listener | None = None
        self._ms_listener: mouse.Listener | None = None
        self.is_recording = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
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
        print(_c("● REC", Fore.RED, Style.BRIGHT)
              + _c(" Recording started.", Fore.WHITE))
        print(_c("  Press Alt+Shift+R or Esc to stop.", Fore.YELLOW))

    def stop(self) -> list[dict]:
        if not self.is_recording:
            return self._events
        self.is_recording = False
        if self._kb_listener:
            self._kb_listener.stop()
        if self._ms_listener:
            self._ms_listener.stop()
        print(_c("■ REC", Fore.RED)
              + _c(f" Recording stopped — {len(self._events)} events captured.",
                   Fore.WHITE))
        return self._events

    def save(self, path: Path = DEFAULT_RECORDING_PATH) -> None:
        """Persist the last recording to *path* as JSON."""
        if not self._events:
            print(_c("[REC] Nothing to save.", Fore.YELLOW))
            return
        duration = self._events[-1]["time"] if self._events else 0.0
        payload = {
            "version": VERSION,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "duration": round(duration, 6),
            "events": self._events,
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(_c(f"[REC] Saved {len(self._events)} events → {path}", Fore.GREEN))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ts(self) -> float:
        """Elapsed seconds since recording started, 6 decimal places."""
        return round(time.perf_counter() - self._start, 6)

    def _record(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)

    # ── Keyboard callbacks ────────────────────────────────────────────────────

    def _on_key_press(self, key) -> None:
        self._record({"type": "key_press", "time": self._ts(), "key": _key_to_str(key)})

    def _on_key_release(self, key) -> None:
        self._record({"type": "key_release", "time": self._ts(), "key": _key_to_str(key)})

    # ── Mouse callbacks ───────────────────────────────────────────────────────

    def _on_mouse_move(self, x: int, y: int) -> None:
        # x and y are absolute screen coordinates provided by pynput.
        self._record({"type": "mouse_move", "time": self._ts(), "x": x, "y": y})

    def _on_mouse_click(self, x: int, y: int, button: Button, pressed: bool) -> None:
        # x and y are absolute screen coordinates provided by pynput.
        self._record({
            "type": "mouse_click",
            "time": self._ts(),
            "x": x,
            "y": y,
            "button": _button_to_str(button),
            "pressed": pressed,
        })

    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        # x and y are the absolute position of the cursor at scroll time.
        # dx and dy are the scroll delta amounts.
        self._record({
            "type": "mouse_scroll",
            "time": self._ts(),
            "x": x,
            "y": y,
            "dx": dx,
            "dy": dy,
        })


# ── Replayer ──────────────────────────────────────────────────────────────────

class Replayer:
    """Replays a recorded event list with high-precision timing.

    Uses a busy-wait (spin-sleep hybrid) in the final microseconds to
    compensate for OS scheduler jitter — important for rhythm games.

    All keyboard keys pressed during replay are tracked and released when
    replay ends (normally or early) to prevent keys from remaining stuck
    down on the host system.
    """

    SPIN_THRESHOLD = 0.001  # spin for the last 1 ms for precision
    STOP_CHECK_INTERVAL = 0.050  # poll stop_event every 50 ms during long sleeps

    def __init__(self):
        self._kb_ctrl = keyboard.Controller()
        self._ms_ctrl = mouse.Controller()
        self.is_replaying = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Keys currently held down by the replayer; cleared on stop/finish.
        self._held_keys: set = set()
        self._held_keys_lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, events: list[dict]) -> None:
        if self.is_replaying:
            print(_c("[PLAY] Already replaying.", Fore.YELLOW))
            return
        if not events:
            print(_c("[PLAY] No events to replay.", Fore.YELLOW))
            return
        self._stop_event.clear()
        self.is_replaying = True
        self._thread = threading.Thread(
            target=self._run,
            args=(events,),
            daemon=True,
            name="replayer",
        )
        self._thread.start()
        print(_c("▶ PLAY", Fore.GREEN, Style.BRIGHT)
              + _c(f" Playback started — {len(events)} events over "
                   f"{events[-1]['time']:.3f}s.", Fore.WHITE))
        print(_c("  Press Esc to stop.", Fore.YELLOW))

    def stop(self) -> None:
        if not self.is_replaying:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        # is_replaying is cleared by _run's finally block; set it here too in
        # case join timed out.
        self.is_replaying = False
        print(_c("■ PLAY", Fore.GREEN) + _c(" Playback stopped.", Fore.WHITE))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _release_held_keys(self) -> None:
        """Release every key the replayer is currently holding down.

        Called from the replayer thread's finally block so that keys are
        always released — whether replay finishes naturally or is aborted.
        This prevents keyboard keys from getting stuck on Windows after
        replay ends.
        """
        with self._held_keys_lock:
            for key in list(self._held_keys):
                try:
                    self._kb_ctrl.release(key)
                except Exception:
                    pass
            self._held_keys.clear()

    def _precise_sleep(self, target_time: float, origin: float) -> None:
        """Sleep until *origin + target_time* with busy-wait for the tail.

        Long coarse sleeps are broken into 50 ms chunks so that a stop signal
        set by another thread is noticed within ~50 ms rather than after the
        full inter-event gap.
        """
        CHUNK = self.STOP_CHECK_INTERVAL
        deadline = origin + target_time
        # Coarse sleep phase — check stop_event between each chunk
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= self.SPIN_THRESHOLD:
                break
            if self._stop_event.is_set():
                return
            time.sleep(min(CHUNK, remaining - self.SPIN_THRESHOLD))
        # Busy-wait for the last millisecond for high precision
        while time.perf_counter() < deadline:
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
            self._release_held_keys()
            self.is_replaying = False

    def _dispatch(self, event: dict) -> None:
        """Execute a single recorded event."""
        etype = event["type"]

        if etype == "key_press":
            key = _str_to_key(event["key"])
            self._kb_ctrl.press(key)
            with self._held_keys_lock:
                self._held_keys.add(key)

        elif etype == "key_release":
            key = _str_to_key(event["key"])
            self._kb_ctrl.release(key)
            with self._held_keys_lock:
                self._held_keys.discard(key)

        elif etype == "mouse_move":
            # Positions are stored as absolute screen coordinates.
            self._ms_ctrl.position = (event["x"], event["y"])

        elif etype == "mouse_click":
            button = _str_to_button(event["button"])
            if event["pressed"]:
                self._ms_ctrl.press(button)
            else:
                self._ms_ctrl.release(button)

        elif etype == "mouse_scroll":
            self._ms_ctrl.scroll(event["dx"], event["dy"])


# ── Session manager ───────────────────────────────────────────────────────────

class Session:
    """Interactive CLI session with a menu-driven recording and playback flow.

    After a recording stops the user is automatically returned to the main
    menu, where they can start another recording, pick a saved one for
    playback, or quit.
    """

    BANNER_WIDTH = 60

    def __init__(self, recordings_dir: Path = RECORDINGS_DIR):
        self._recordings_dir = Path(recordings_dir)
        self._recordings_dir.mkdir(parents=True, exist_ok=True)
        self._recorder = Recorder()
        self._replayer = Replayer()
        self._events: list[dict] = []

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _available_recordings(self) -> list[Path]:
        """Return all *.json files in the recordings directory, newest first."""
        return sorted(self._recordings_dir.glob("*.json"), reverse=True)

    @staticmethod
    def _new_recording_name() -> str:
        """Generate a timestamped recording filename."""
        return datetime.now().strftime("recording_%Y%m%d_%H%M%S.json")

    # ── Display ───────────────────────────────────────────────────────────────

    def _print_banner(self) -> None:
        w = self.BANNER_WIDTH
        print(_c("━" * w, Fore.CYAN))
        print(_c("  KeystrokeReplay", Fore.CYAN, Style.BRIGHT)
              + _c("  —  high-precision input recorder & replayer", Fore.CYAN))
        print(_c("━" * w, Fore.CYAN))

    def _print_main_menu(self) -> None:
        print()
        print(_c("┌─ Main Menu " + "─" * 47, Fore.BLUE))
        print(_c("│", Fore.BLUE)
              + _c("  [1]", Fore.GREEN, Style.BRIGHT)
              + "  Start new recording")
        print(_c("│", Fore.BLUE)
              + _c("  [2]", Fore.GREEN, Style.BRIGHT)
              + "  Play back a recording")
        print(_c("│", Fore.BLUE)
              + _c("  [3]", Fore.CYAN, Style.BRIGHT)
              + "  List saved recordings")
        print(_c("│", Fore.BLUE)
              + _c("  [4]", Fore.RED, Style.BRIGHT)
              + "  Quit")
        print(_c("└" + "─" * 58, Fore.BLUE))

    # ── Recording flow ────────────────────────────────────────────────────────

    def _do_record(self) -> None:
        """Start recording and block until the user stops it, then save."""
        stop_event = threading.Event()

        def on_stop() -> None:
            if self._recorder.is_recording:
                self._events = self._recorder.stop()
            stop_event.set()

        self._recorder.start()
        with keyboard.GlobalHotKeys({
            "<alt>+<shift>+r": on_stop,
            "<esc>": on_stop,
        }):
            stop_event.wait()

        if self._events:
            path = self._recordings_dir / self._new_recording_name()
            self._recorder.save(path)
            print(_c(f"  Saved as: {path.name}", Fore.GREEN))

    # ── Playback flow ─────────────────────────────────────────────────────────

    def _do_playback(self) -> None:
        """Let the user pick a saved recording and play it back."""
        recordings = self._available_recordings()
        if not recordings:
            print(_c("  No recordings found. Record something first!", Fore.YELLOW))
            return

        print()
        print(_c("  Available recordings:", Fore.CYAN, Style.BRIGHT))
        for i, r in enumerate(recordings, 1):
            print(_c(f"    [{i}]", Fore.GREEN) + f"  {r.name}")
        print(_c("    [0]", Fore.RED) + "  Back to main menu")

        while True:
            raw = input(_c("\n  Select recording: ", Fore.CYAN, Style.BRIGHT)).strip()
            try:
                idx = int(raw)
            except ValueError:
                print(_c("  Please enter a number.", Fore.RED))
                continue
            if idx == 0:
                return
            if 1 <= idx <= len(recordings):
                chosen = recordings[idx - 1]
                break
            print(_c(f"  Enter a number between 0 and {len(recordings)}.", Fore.RED))

        events = _load_events(chosen)
        if not events:
            print(_c("  Failed to load recording.", Fore.RED))
            return

        stop_event = threading.Event()

        def on_stop() -> None:
            self._replayer.stop()
            stop_event.set()

        self._replayer.start(events)
        with keyboard.GlobalHotKeys({"<esc>": on_stop}):
            while self._replayer.is_replaying and not stop_event.is_set():
                time.sleep(0.05)

        if not stop_event.is_set():
            print(_c("  Playback complete.", Fore.GREEN))

    # ── List recordings ───────────────────────────────────────────────────────

    def _do_list(self) -> None:
        recordings = self._available_recordings()
        print()
        if not recordings:
            print(_c("  No recordings found.", Fore.YELLOW))
            return
        print(_c(f"  Recordings in {self._recordings_dir}:", Fore.CYAN, Style.BRIGHT))
        for r in recordings:
            size = r.stat().st_size
            print(_c("    • ", Fore.CYAN) + f"{r.name}"
                  + _c(f"  ({size:,} bytes)", Fore.WHITE))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Show the interactive main menu and handle user input in a loop."""
        self._print_banner()
        try:
            while True:
                self._print_main_menu()
                choice = input(_c("  Select: ", Fore.CYAN, Style.BRIGHT)).strip()
                if choice == "1":
                    self._do_record()
                elif choice == "2":
                    self._do_playback()
                elif choice == "3":
                    self._do_list()
                elif choice == "4":
                    break
                else:
                    print(_c("  Invalid choice. Enter 1–4.", Fore.RED))
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            # Ensure nothing is left running when the session exits.
            if self._recorder.is_recording:
                self._recorder.stop()
            if self._replayer.is_replaying:
                self._replayer.stop()
            print(_c("\n  Goodbye!", Fore.CYAN, Style.BRIGHT))


# ── JSON I/O ─────────────────────────────────────────────────────────────────

def _load_events(path: Path) -> list[dict]:
    """Load events from a JSON recording file."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        events = data.get("events", [])
        print(_c(f"[LOAD] Loaded {len(events)} events from {path}", Fore.CYAN))
        return events
    except (json.JSONDecodeError, KeyError) as exc:
        print(_c(f"[ERROR] Failed to load {path}: {exc}", Fore.RED))
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
        "--play",
        metavar="PATH",
        help="Immediately replay a recording from PATH and exit.",
    )
    parser.add_argument(
        "--recordings-dir",
        metavar="DIR",
        default=str(RECORDINGS_DIR),
        help=f"Directory for saving/loading recordings (default: {RECORDINGS_DIR})",
    )
    args = parser.parse_args()

    if args.play:
        # Non-interactive one-shot replay mode
        events = _load_events(Path(args.play))
        if not events:
            sys.exit(1)
        replayer = Replayer()
        done = threading.Event()

        def _wait_for_done() -> None:
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

    # Interactive mode — show the menu
    session = Session(recordings_dir=Path(args.recordings_dir))
    session.run()


if __name__ == "__main__":
    main()

