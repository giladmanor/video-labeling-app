"""Training-window counts for the panel (docs/design.md).

TODO: move to a module shared with the training pipeline when it exists, so that the
panel and the windowing step use the same code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .store import frame_labels


@dataclass(frozen=True)
class WindowConfig:
    name: str
    length_s: float
    stride_s: float
    end_part_s: float
    positive_min: float = 0.5   # end_pos_fraction >= positive_min -> positive


CONFIGS = {
    "causal-2s": WindowConfig("causal-2s", length_s=2.0, stride_s=0.5, end_part_s=0.5),
}


def window_labels(labels: list[int | None], fps: float, cfg: WindowConfig) -> list[int]:
    """Labels (1, 0, -1 = ignore) of the causal windows that fit inside one watched range.

    A window ends at frame e (excluded) and starts at e - L. Its label comes from the
    last end_part_s of the window (the "now" part).
    """
    length = max(1, round(cfg.length_s * fps))
    stride = max(1, round(cfg.stride_s * fps))
    end_part = max(1, round(cfg.end_part_s * fps))
    out: list[int] = []
    n = len(labels)
    # Watched runs: maximal runs of frames that have a label.
    f = 0
    while f < n:
        if labels[f] is None:
            f += 1
            continue
        start = f
        while f < n and labels[f] is not None:
            f += 1
        for e in range(start + length, f + 1, stride):
            tail = labels[e - end_part:e]
            pos = sum(1 for v in tail if v == 1) / len(tail)
            if pos >= cfg.positive_min:
                out.append(1)
            elif all(v == 0 for v in tail):
                out.append(0)
            else:
                out.append(-1)
    return out


def summarize(labels_by_video: list[dict], cfg: WindowConfig) -> dict:
    """Counts for a list of label files."""
    pos = neg = ign = events = hard_neg = short_parts = 0
    watched_s = 0.0
    for label in labels_by_video:
        fps = float(label["fps"])
        wl = window_labels(frame_labels(label), fps, cfg)
        pos += wl.count(1)
        neg += wl.count(0)
        ign += wl.count(-1)
        # Only watched frames are exported, so a segment counts only if a person saw part of it.
        seen = [s for s in label["segments"]
                if any(a <= s["end_frame"] and s["start_frame"] <= b for a, b in label["watched"])]
        events += sum(1 for s in seen if s["label"] == 1)
        hard_neg += sum(1 for s in seen if s["origin"] == "grok_rejected")
        min_len = math.ceil(cfg.length_s * fps)
        for a, b in label["watched"]:
            watched_s += (b - a + 1) / fps
            if b - a + 1 < min_len:
                short_parts += 1
    total = pos + neg
    return {
        "config": cfg.name,
        "source_videos": len(labels_by_video),
        "phone_events": events,
        "positive_windows": pos,
        "negative_windows": neg,
        "ignored_windows": ign,
        "hard_negatives": hard_neg,
        "watched_s": round(watched_s, 1),
        "short_watched_parts": short_parts,
        "positive_fraction": round(pos / total, 3) if total else None,
    }
