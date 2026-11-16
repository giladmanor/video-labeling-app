"""Label files: one JSON file for each video in the workspace (docs/design.md).

Frame numbers are on the normalized video. A segment covers frames start_frame..end_frame,
both included. Watched ranges use the same convention.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .workspace import SEED_LABELS, write_json_atomic

SEED_VALUES = {x["value"] for x in SEED_LABELS}
LABEL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
# Keys of the labeling screen that a label cannot use (docs/design.md, keys). The ignore label
# may use "u": U already makes a segment ignore.
RESERVED_KEYS = set(" anriowbtsp?[]")
ORIGINS = {"grok_accepted", "grok_rejected", "manual", "watched", "unsure"}
STATUSES = {"todo", "done", "skip"}
DECISIONS = {"accepted", "rejected", "unsure"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_label(video_id: str, labeler: str, fps: float, n_frames: int, prelabel_run: str | None) -> dict:
    return {
        "video_id": video_id,
        "labeler": labeler,
        "fps": fps,
        "n_frames": n_frames,
        "prelabel_run": prelabel_run,
        "status": "todo",
        "skip_reason": "",
        "note": "",
        "segments": [],
        "watched": [],
        "decisions": {},
        "created_at": now(),
        "updated_at": now(),
    }


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def save(path: Path, label: dict, values: set[int] = SEED_VALUES) -> dict:
    label = validate(label, values)
    label["updated_at"] = now()
    write_json_atomic(path, label)
    return label


def merge_ranges(ranges: list[list[int]], n_frames: int) -> list[list[int]]:
    """Sort, clamp, and join frame ranges that overlap or touch."""
    out: list[list[int]] = []
    for a, b in sorted((max(0, int(a)), min(n_frames - 1, int(b))) for a, b in ranges):
        if a > b:
            continue
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def check_label_config(labels: list[dict]) -> list[dict]:
    """Check the label list of the Setup page. Raises ValueError. Returns a cleaned copy.

    0 (background) and -1 (ignore) must exist; event labels use the values 1 to 99.
    Each label has one keyboard key (one character, not a key of the labeling screen) and a
    color ("#rrggbb").
    """
    out, names, keys = [], set(), set()
    for x in labels:
        value, name = x.get("value"), str(x.get("name", "")).strip()
        key = str(x.get("key", "")).strip().lower()
        color = str(x.get("color", "")).strip().lower()
        if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= 99:
            raise ValueError(f"label value {value!r} is not -1, 0, or 1 to 99")
        if not LABEL_NAME_RE.match(name):
            raise ValueError(f"label name {name!r}: use 1 to 32 letters, digits, _ or -")
        if name in names:
            raise ValueError(f"label name {name!r} is used twice")
        if len(key) != 1 or not key.isprintable():
            raise ValueError(f"label {name}: the key must be one character")
        if key in RESERVED_KEYS and not (key == "u" and value == -1):
            raise ValueError(f"label {name}: the key {key!r} is already a key of the labeling screen")
        if key in keys:
            raise ValueError(f"the key {key!r} is used twice")
        if not COLOR_RE.match(color):
            raise ValueError(f"label {name}: the color must be #rrggbb")
        names.add(name)
        keys.add(key)
        out.append({"value": value, "name": name, "key": key, "color": color})
    values = [x["value"] for x in out]
    if len(set(values)) != len(values):
        raise ValueError("each label value can be used only once")
    if 0 not in values or -1 not in values:
        raise ValueError("the labels 0 (background) and -1 (ignore) are required")
    if not any(v >= 1 for v in values):
        raise ValueError("add at least one event label")
    return sorted(out, key=lambda x: x["value"] if x["value"] >= 1 else 1000 - x["value"])   # events, 0, -1


def used_values(labels: Iterable[dict]) -> set[int]:
    """The segment label values used in the given label files."""
    return {s["label"] for label in labels for s in label["segments"]}


def validate(label: dict, values: set[int] = SEED_VALUES) -> dict:
    """Check a label sent by the page. Raises ValueError. Returns a cleaned copy.

    `values` are the label values of the workspace (Setup page).
    """
    n = int(label["n_frames"])
    if label.get("status") not in STATUSES:
        raise ValueError(f"bad status {label.get('status')!r}")
    segments = []
    for s in label.get("segments", []):
        a, b = int(s["start_frame"]), int(s["end_frame"])
        if not 0 <= a <= b < n:
            raise ValueError(f"segment {a}-{b} is outside 0-{n - 1}")
        if isinstance(s.get("label"), bool) or s.get("label") not in values:
            raise ValueError(f"bad label {s.get('label')!r}")
        if s.get("origin") not in ORIGINS:
            raise ValueError(f"bad origin {s.get('origin')!r}")
        seg = {
            "start_frame": a, "end_frame": b, "label": s["label"],
            "tags": sorted(set(s.get("tags", []))), "origin": s["origin"],
        }
        for k in ("candidate_id", "candidate_confidence", "note"):
            if s.get(k) not in (None, ""):
                seg[k] = s[k]
        segments.append(seg)
    segments.sort(key=lambda s: s["start_frame"])
    for prev, cur in zip(segments, segments[1:]):
        if cur["start_frame"] <= prev["end_frame"]:
            raise ValueError(f"segments overlap at frame {cur['start_frame']}")
    decisions = {k: v for k, v in label.get("decisions", {}).items() if v in DECISIONS}
    return {
        **label,
        "segments": segments,
        "watched": merge_ranges(label.get("watched", []), n),
        "decisions": decisions,
    }


def filled_segments(label: dict) -> list[dict]:
    """The explicit segments plus "driving" for watched frames that no segment covers.

    Unwatched frames stay unlabeled (docs/design.md).
    """
    n = int(label["n_frames"])
    covered = [0] * n
    for s in label["segments"]:
        for f in range(s["start_frame"], s["end_frame"] + 1):
            covered[f] = 1
    fill: list[list[int]] = []
    for a, b in label["watched"]:
        run_start = None
        for f in range(a, b + 2):
            free = f <= b and not covered[f]
            if free and run_start is None:
                run_start = f
            elif not free and run_start is not None:
                fill.append([run_start, f - 1])
                run_start = None
    out = list(label["segments"]) + [
        {"start_frame": a, "end_frame": b, "label": 0, "tags": [], "origin": "watched"} for a, b in fill
    ]
    return sorted(out, key=lambda s: s["start_frame"])


def frame_labels(label: dict) -> list[int | None]:
    """One value for each frame: 1, 0, -1, or None (not watched, so not labeled)."""
    n = int(label["n_frames"])
    seg: list[int | None] = [None] * n
    for s in filled_segments(label):
        for f in range(s["start_frame"], s["end_frame"] + 1):
            seg[f] = s["label"]
    out: list[int | None] = [None] * n
    for a, b in label["watched"]:
        out[a:b + 1] = seg[a:b + 1]
    return out
