"""Export: cut the watched parts of the done videos into clips and write label files.

Export (docs/design.md). Each watched range of a video with status "done" becomes
one clip. The clip frames are cut from the normalized video by frame number, so the label
frames stay exact. The manifests use the format of docs/design.md.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, ffmpeg, store
from .jobs import sha256_file
from .windows import CONFIGS, summarize
from .workspace import Workspace, write_json_atomic

LABEL_NAMES = {1: "phone", 0: "driving", -1: "ignore"}


class NothingToExport(ValueError):
    pass


def clip_segments(label: dict, a: int, b: int) -> list[dict]:
    """The filled segments inside frames a..b, moved to clip frame numbers (0 = frame a)."""
    out = []
    for s in store.filled_segments(label):
        lo, hi = max(a, s["start_frame"]), min(b, s["end_frame"])
        if lo > hi:
            continue
        seg = {**s, "start_frame": lo - a, "end_frame": hi - a}
        out.append(seg)
    covered = sum(s["end_frame"] - s["start_frame"] + 1 for s in out)
    if covered != b - a + 1:
        raise ValueError(f"clip {a}-{b}: segments cover {covered} of {b - a + 1} frames")
    return out


def cut_clip(src: Path, dst: Path, a: int, b: int, fps: float) -> None:
    """Encode frames a..b of the normalized video. The seek point is half a frame before
    frame a, so the first frame after an accurate seek is frame a."""
    tmp = dst.with_name(dst.stem + ".part.mp4")
    ffmpeg._run([
        ffmpeg.binaries()[0], "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-ss", f"{max(0.0, (a - 0.5) / fps):.6f}", "-i", str(src),
        "-frames:v", str(b - a + 1), "-map", "0:v:0",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-g", str(round(fps)), "-keyint_min", str(round(fps)), "-sc_threshold", "0",
        "-an", "-movflags", "+faststart", str(tmp),
    ])
    tmp.replace(dst)


def export(ws: Workspace, name: str | None = None) -> dict:
    cfg = CONFIGS[ws.settings.window_config]
    done = [(row, label) for row in ws.videos()
            if (label := store.load(ws.label_path(row["video_id"]))) and label["status"] == "done"]
    if not done:
        raise NothingToExport("No video is marked done yet. Finish a video with Done (Enter) first.")
    now = datetime.now(timezone.utc)
    name = name or f"{ws.settings.labeler or 'labeler'}_{now.strftime('%Y%m%d-%H%M%S')}"
    root = ws.exports_dir / name
    (root / "videos").mkdir(parents=True, exist_ok=False)
    (root / "labels").mkdir()
    (root / "manifests").mkdir()

    video_rows, segment_rows, done_labels = [], [], []
    dropped_short = 0
    for row, label in done:
        done_labels.append(label)
        fps = float(label["fps"])
        min_frames = math.ceil(cfg.length_s * fps)
        for a, b in label["watched"]:
            if b - a + 1 < min_frames:
                dropped_short += 1
                continue
            clip_id = f"{row['video_id']}_{a:06d}"
            segments = clip_segments(label, a, b)
            dst = root / "videos" / f"{clip_id}.mp4"
            cut_clip(ws.video_path(row["video_id"]), dst, a, b, fps)
            n_frames = b - a + 1
            write_json_atomic(root / "labels" / f"{clip_id}.json", {
                "clip_id": clip_id,
                "parent_video_id": row["video_id"],
                "parent_sha256": row["sha256"],
                "parent_file": row["rel_path"],
                "library": Path(row["library"]).name,
                "group_id": row["video_id"],
                "clip_start_s": round(a / fps, 6),
                "clip_start_frame": a,
                "fps": fps,
                "n_frames": n_frames,
                "duration_s": round(n_frames / fps, 6),
                "labeler": label["labeler"],
                "audit": False,
                "labeled_at": label["updated_at"],
                "label_names": {str(k): v for k, v in LABEL_NAMES.items()},
                "segments": segments,
            })
            video_rows.append({
                "video_id": clip_id,
                "source_file": f"videos/{clip_id}.mp4",
                "sha256": sha256_file(dst),
                "fps": fps,
                "n_frames": n_frames,
                "duration_s": round(n_frames / fps, 6),
                "width": row["width"],
                "height": row["height"],
                "group_id": row["video_id"],
                "label_type": "segments",
                "parent_video_id": row["video_id"],
                "parent_sha256": row["sha256"],
                "clip_start_s": round(a / fps, 6),
                "audit": 0,
                "labeler": label["labeler"],
            })
            for s in segments:
                segment_rows.append({
                    "video_id": clip_id,
                    "start_s": round(s["start_frame"] / fps, 6),
                    "end_s": round((s["end_frame"] + 1) / fps, 6),
                    "start_frame": s["start_frame"],
                    "end_frame": s["end_frame"],
                    "label": s["label"],
                    "source": "human",
                    "origin": s["origin"],
                    "tags": ";".join(s.get("tags", [])),
                })

    _write_csv(root / "manifests" / "videos.csv", video_rows, [
        "video_id", "source_file", "sha256", "fps", "n_frames", "duration_s", "width", "height",
        "group_id", "label_type", "parent_video_id", "parent_sha256", "clip_start_s", "audit", "labeler",
    ])
    _write_csv(root / "manifests" / "segments.csv", segment_rows, [
        "video_id", "start_s", "end_s", "start_frame", "end_frame", "label", "source", "origin", "tags",
    ])
    info = {
        "name": name,
        "path": str(root),
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_version": __version__,
        "ffmpeg": ffmpeg.version(),
        "labeler": ws.settings.labeler,
        "window_config": cfg.name,
        "done_videos": len(done_labels),
        "clips": len(video_rows),
        "dropped_short_parts": dropped_short,
        "label_names": {str(k): v for k, v in LABEL_NAMES.items()},
        "counts": summarize(done_labels, cfg),
        "prelabel_runs": sorted({lab.get("prelabel_run") or "" for lab in done_labels} - {""}),
    }
    write_json_atomic(root / "export.json", info)
    return info


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
