"""Label files: one JSON file for each video in the workspace (docs/design.md).

Frame numbers are on the normalized video. A segment covers frames start_frame..end_frame,
both included. Watched ranges use the same convention.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .workspace import write_json_atomic

LABELS = {1: "phone", 0: "driving", -1: "unsure"}
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


def save(path: Path, label: dict) -> dict:
    label = validate(label)
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


def validate(label: dict) -> dict:
    """Check a label sent by the page. Raises ValueError. Returns a cleaned copy."""
    n = int(label["n_frames"])
    if label.get("status") not in STATUSES:
        raise ValueError(f"bad status {label.get('status')!r}")
    segments = []
    for s in label.get("segments", []):
        a, b = int(s["start_frame"]), int(s["end_frame"])
        if not 0 <= a <= b < n:
            raise ValueError(f"segment {a}-{b} is outside 0-{n - 1}")
        if s.get("label") not in LABELS:
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
