"""Tests for keystroke_replay.py — focus on serialisation, recording logic,
and replay timing accuracy. All tests are fully offline (no real input
devices are created; pynput listeners are patched away)."""

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import keystroke_replay as kr
from keystroke_replay import (
    Recorder,
    Replayer,
    _button_to_str,
    _key_to_str,
    _load_events,
    _str_to_button,
    _str_to_key,
)
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestKeySerialization(unittest.TestCase):

    def test_printable_char_roundtrip(self):
        key = KeyCode.from_char("a")
        self.assertEqual(_str_to_key(_key_to_str(key)).char, "a")

    def test_special_key_roundtrip(self):
        for special in (Key.space, Key.enter, Key.shift, Key.ctrl):
            serialised = _key_to_str(special)
            restored = _str_to_key(serialised)
            self.assertEqual(restored, special)

    def test_vk_only_key(self):
        key = KeyCode(vk=65)  # no .char
        s = _key_to_str(key)
        self.assertTrue(s.startswith("<vk:"))
        restored = _str_to_key(s)
        self.assertEqual(restored.vk, 65)

    def test_button_roundtrip(self):
        for btn in (Button.left, Button.right, Button.middle):
            self.assertEqual(_str_to_button(_button_to_str(btn)), btn)


# ─────────────────────────────────────────────────────────────────────────────
# Recorder
# ─────────────────────────────────────────────────────────────────────────────

class TestRecorder(unittest.TestCase):

    @patch("keystroke_replay.keyboard.Listener")
    @patch("keystroke_replay.mouse.Listener")
    def test_start_stop_returns_events(self, mock_ms, mock_kb):
        for m in (mock_kb, mock_ms):
            m.return_value.start = MagicMock()
            m.return_value.stop = MagicMock()

        rec = Recorder()
        rec.start()
        self.assertTrue(rec.is_recording)

        # Simulate events by calling callbacks directly
        rec._on_key_press(KeyCode.from_char("z"))
        rec._on_key_release(KeyCode.from_char("z"))
        rec._on_mouse_move(100, 200)
        rec._on_mouse_click(100, 200, Button.left, True)
        rec._on_mouse_click(100, 200, Button.left, False)
        rec._on_mouse_scroll(100, 200, 0, -1)

        events = rec.stop()
        self.assertFalse(rec.is_recording)
        self.assertEqual(len(events), 6)

        event_types = [e["type"] for e in events]
        self.assertIn("key_press", event_types)
        self.assertIn("key_release", event_types)
        self.assertIn("mouse_move", event_types)
        self.assertIn("mouse_click", event_types)
        self.assertIn("mouse_scroll", event_types)

    @patch("keystroke_replay.keyboard.Listener")
    @patch("keystroke_replay.mouse.Listener")
    def test_timestamps_are_monotonic(self, mock_ms, mock_kb):
        for m in (mock_kb, mock_ms):
            m.return_value.start = MagicMock()
            m.return_value.stop = MagicMock()

        rec = Recorder()
        rec.start()
        for char in "abcde":
            time.sleep(0.002)
            rec._on_key_press(KeyCode.from_char(char))
        events = rec.stop()

        times = [e["time"] for e in events]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(t >= 0 for t in times))

    @patch("keystroke_replay.keyboard.Listener")
    @patch("keystroke_replay.mouse.Listener")
    def test_save_creates_valid_json(self, mock_ms, mock_kb):
        import tempfile

        for m in (mock_kb, mock_ms):
            m.return_value.start = MagicMock()
            m.return_value.stop = MagicMock()

        rec = Recorder()
        rec.start()
        rec._on_key_press(KeyCode.from_char("x"))
        rec.stop()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            rec.save(tmp)
            data = json.loads(tmp.read_text())
            self.assertEqual(data["version"], 1)
            self.assertIn("recorded_at", data)
            self.assertIn("duration", data)
            self.assertIsInstance(data["events"], list)
            self.assertEqual(len(data["events"]), 1)
        finally:
            tmp.unlink(missing_ok=True)

    @patch("keystroke_replay.keyboard.Listener")
    @patch("keystroke_replay.mouse.Listener")
    def test_event_fields_are_correct(self, mock_ms, mock_kb):
        for m in (mock_kb, mock_ms):
            m.return_value.start = MagicMock()
            m.return_value.stop = MagicMock()

        rec = Recorder()
        rec.start()
        rec._on_key_press(KeyCode.from_char("q"))
        rec._on_mouse_click(50, 75, Button.right, True)
        rec._on_mouse_scroll(50, 75, 1, 0)
        events = rec.stop()

        kp = next(e for e in events if e["type"] == "key_press")
        self.assertEqual(kp["key"], "q")
        self.assertIn("time", kp)

        mc = next(e for e in events if e["type"] == "mouse_click")
        self.assertEqual(mc["x"], 50)
        self.assertEqual(mc["y"], 75)
        self.assertEqual(mc["button"], "right")
        self.assertTrue(mc["pressed"])

        ms = next(e for e in events if e["type"] == "mouse_scroll")
        self.assertEqual(ms["dx"], 1)
        self.assertEqual(ms["dy"], 0)

    @patch("keystroke_replay.keyboard.Listener")
    @patch("keystroke_replay.mouse.Listener")
    def test_double_start_is_noop(self, mock_ms, mock_kb):
        for m in (mock_kb, mock_ms):
            m.return_value.start = MagicMock()
            m.return_value.stop = MagicMock()

        rec = Recorder()
        rec.start()
        rec._on_key_press(KeyCode.from_char("a"))
        rec.start()  # second start should be a no-op
        events = rec.stop()
        self.assertEqual(len(events), 1)

    @patch("keystroke_replay.keyboard.Listener")
    @patch("keystroke_replay.mouse.Listener")
    def test_stop_when_not_recording_returns_empty(self, mock_ms, mock_kb):
        for m in (mock_kb, mock_ms):
            m.return_value.start = MagicMock()
            m.return_value.stop = MagicMock()

        rec = Recorder()
        events = rec.stop()
        self.assertEqual(events, [])


# ─────────────────────────────────────────────────────────────────────────────
# Replayer
# ─────────────────────────────────────────────────────────────────────────────

class TestReplayer(unittest.TestCase):

    def _sample_events(self):
        return [
            {"type": "key_press",    "time": 0.00, "key": "a"},
            {"type": "key_release",  "time": 0.05, "key": "a"},
            {"type": "mouse_move",   "time": 0.10, "x": 100, "y": 200},
            {"type": "mouse_click",  "time": 0.15, "x": 100, "y": 200,
             "button": "left", "pressed": True},
            {"type": "mouse_click",  "time": 0.20, "x": 100, "y": 200,
             "button": "left", "pressed": False},
            {"type": "mouse_scroll", "time": 0.25, "x": 100, "y": 200,
             "dx": 0, "dy": -1},
        ]

    @patch("keystroke_replay.keyboard.Controller")
    @patch("keystroke_replay.mouse.Controller")
    def test_all_events_dispatched(self, mock_ms_ctrl_cls, mock_kb_ctrl_cls):
        replayer = Replayer()
        replayer._kb_ctrl = mock_kb_ctrl_cls()
        replayer._ms_ctrl = mock_ms_ctrl_cls()

        events = self._sample_events()
        replayer.start(events)

        deadline = time.time() + 3.0
        while replayer.is_replaying and time.time() < deadline:
            time.sleep(0.05)

        self.assertFalse(replayer.is_replaying)
        self.assertTrue(replayer._kb_ctrl.press.called)
        self.assertTrue(replayer._kb_ctrl.release.called)

    @patch("keystroke_replay.keyboard.Controller")
    @patch("keystroke_replay.mouse.Controller")
    def test_stop_aborts_replay(self, mock_ms_ctrl_cls, mock_kb_ctrl_cls):
        replayer = Replayer()
        replayer._kb_ctrl = mock_kb_ctrl_cls()
        replayer._ms_ctrl = mock_ms_ctrl_cls()

        # Events spanning several seconds
        events = [{"type": "key_press", "time": i * 0.5, "key": "a"}
                  for i in range(20)]
        replayer.start(events)
        time.sleep(0.1)
        replayer.stop()
        self.assertFalse(replayer.is_replaying)

    @patch("keystroke_replay.keyboard.Controller")
    @patch("keystroke_replay.mouse.Controller")
    def test_replay_empty_events(self, mock_ms_ctrl_cls, mock_kb_ctrl_cls):
        replayer = Replayer()
        replayer._kb_ctrl = mock_kb_ctrl_cls()
        replayer._ms_ctrl = mock_ms_ctrl_cls()
        replayer.start([])
        self.assertFalse(replayer.is_replaying)

    @patch("keystroke_replay.keyboard.Controller")
    @patch("keystroke_replay.mouse.Controller")
    def test_timing_accuracy(self, mock_ms_ctrl_cls, mock_kb_ctrl_cls):
        """Replay timing should land within 5 ms of the scheduled time."""
        actual_times: list[float] = []
        origin: list[float] = [0.0]

        def capture_dispatch(event):
            actual_times.append(time.perf_counter() - origin[0])

        replayer = Replayer()
        replayer._kb_ctrl = mock_kb_ctrl_cls()
        replayer._ms_ctrl = mock_ms_ctrl_cls()
        replayer._dispatch = capture_dispatch  # type: ignore[method-assign]

        scheduled = [0.0, 0.05, 0.10, 0.15, 0.20]
        events = [{"type": "key_press", "time": t, "key": "z"}
                  for t in scheduled]

        origin[0] = time.perf_counter()
        replayer.start(events)
        deadline = time.time() + 3.0
        while replayer.is_replaying and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(len(actual_times), len(scheduled))
        for exp, act in zip(scheduled, actual_times):
            self.assertAlmostEqual(
                act, exp, delta=0.005,
                msg=f"Event at {exp}s was dispatched at {act:.6f}s"
            )


# ─────────────────────────────────────────────────────────────────────────────
# JSON loading
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadEvents(unittest.TestCase):

    def test_missing_file_returns_empty(self):
        result = _load_events(Path("/tmp/does_not_exist_xyzzy.json"))
        self.assertEqual(result, [])

    def test_loads_valid_file(self):
        import tempfile
        events = [{"type": "key_press", "time": 0.0, "key": "a"}]
        payload = {
            "version": 1,
            "recorded_at": "2024-01-01T00:00:00+00:00",
            "duration": 0.0,
            "events": events,
        }
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8"
        ) as f:
            json.dump(payload, f)
            tmp = Path(f.name)
        try:
            loaded = _load_events(tmp)
            self.assertEqual(loaded, events)
        finally:
            tmp.unlink(missing_ok=True)

    def test_malformed_json_returns_empty(self):
        import tempfile
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write("{not valid json")
            tmp = Path(f.name)
        try:
            result = _load_events(tmp)
            self.assertEqual(result, [])
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
