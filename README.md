# KeystrokeReplay

High-precision keyboard and mouse recorder/replayer written in Python.
Built for rhythm games like **osu!**, **Geometry Dash**, and any other
application where timing accuracy matters.

## Features

- Records **keyboard presses/releases**, **mouse clicks**, **mouse movement**,
  and **scroll wheel** events
- Stores recordings as human-readable **JSON** files
- **Sub-millisecond replay timing** — hybrid sleep + busy-wait eliminates OS
  scheduler jitter during playback
- **Global hotkeys** work even when the script is not in focus:

| Hotkey | Action |
|---|---|
| `Alt + Shift + R` | Toggle recording on / off |
| `Alt + Shift + P` | Start / stop playback |
| `Escape` | Stop the active recording or playback |
| `Ctrl + C` | Quit the script |

## Requirements

- Python 3.10+
- [pynput](https://pynput.readthedocs.io/)

```bash
pip install -r requirements.txt
```

> **Linux users:** pynput needs access to the X server or a Wayland
> compatibility layer. Running under a normal desktop session is sufficient.
> For Wayland, set `PYNPUT_BACKEND=xorg` or use XWayland.

## Usage

### Interactive mode (recommended)

```bash
python keystroke_replay.py
```

Use the hotkeys above to start/stop recording and playback. Recordings are
saved automatically to `recording.json` when you stop.

### Custom file path

```bash
python keystroke_replay.py --file my_run.json
```

### One-shot replay (non-interactive)

```bash
python keystroke_replay.py --play my_run.json
```

Replays the file and exits. Useful for scripts or CI pipelines.

## JSON recording format

```json
{
  "version": 1,
  "recorded_at": "2024-01-01T00:00:00+00:00",
  "duration": 12.345678,
  "events": [
    {"type": "key_press",    "time": 0.0,      "key": "z"},
    {"type": "key_release",  "time": 0.120456, "key": "z"},
    {"type": "mouse_move",   "time": 0.250000, "x": 960, "y": 540},
    {"type": "mouse_click",  "time": 0.300000, "x": 960, "y": 540, "button": "left", "pressed": true},
    {"type": "mouse_click",  "time": 0.350000, "x": 960, "y": 540, "button": "left", "pressed": false},
    {"type": "mouse_scroll", "time": 0.400000, "x": 960, "y": 540, "dx": 0, "dy": 1}
  ]
}
```

All `time` values are seconds elapsed since the start of the recording,
stored with microsecond precision.

## Tips for rhythm games

- Record your inputs during a practice run, then replay to verify accuracy.
- The busy-wait tail in the replayer keeps per-event timing error under
  **~20 µs** on a typical desktop system.
- Avoid running other CPU-heavy tasks during replay to minimise jitter.
- On Windows, consider setting the script's process priority to
  `HIGH_PRIORITY_CLASS` via Task Manager for the best results.
