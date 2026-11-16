"""Export: cut the watched parts of the videos into clips and write label files.

Export (docs/design.md section 8). Each watched range of a video becomes one clip: watched time
is time that a person has seen and checked, so it does not need the status "done". Skipped
videos are left out. The clip frames are cut from the normalized video by frame number, so the label
frames stay exact. The manifests use the format of docs/design.md.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, ffmpeg, store
from .jobs import sha256_file
from .windows import CONFIGS, summarize
from .workspace import Workspace, write_json_atomic

# The clip encode (docs/design.md section 8.1); export.json records it.
ENCODE = {"codec": "libx264", "crf": 18, "preset": "fast", "pix_fmt": "yuv420p", "gop_s": 1, "audio": False}


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
    frame a, so the first frame after an accurate seek is frame a. Clip frame i gets the time
    i / fps exactly (the seek leaves an offset), so time-based readers find the labeled frame."""
    tmp = dst.with_name(dst.stem + ".part.mp4")
    gop = str(round(fps * ENCODE["gop_s"]))
    ffmpeg._run([
        ffmpeg.binaries()[0], "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-ss", f"{max(0.0, (a - 0.5) / fps):.6f}", "-i", str(src),
        "-frames:v", str(b - a + 1), "-map", "0:v:0", "-vf", f"setpts=N/({fps}*TB)",
        "-c:v", ENCODE["codec"], "-crf", str(ENCODE["crf"]), "-preset", ENCODE["preset"], "-pix_fmt", ENCODE["pix_fmt"],
        "-g", gop, "-keyint_min", gop, "-sc_threshold", "0",
        "-an", "-movflags", "+faststart", str(tmp),
    ])
    tmp.replace(dst)


def trainable(ws: Workspace) -> list[tuple]:
    """(video row, label) for each video with watched time that is not skipped."""
    return [(row, label) for row in ws.videos()
            if (label := store.load(ws.label_path(row["video_id"]))) and label["status"] != "skip" and label["watched"]]


def parts(ws: Workspace):
    """Each watched range that becomes a clip: (row, label, a, b), and the number of short ranges."""
    min_s = CONFIGS[ws.settings.window_config].length_s
    out, short = [], 0
    for row, label in trainable(ws):
        for a, b in label["watched"]:
            if b - a + 1 < math.ceil(min_s * float(label["fps"])):
                short += 1
            else:
                out.append((row, label, a, b))
    return out, short


def stats(ws: Workspace) -> dict:
    """What an export would hold now: the seconds and, for each label, its segments and seconds."""
    found, short = parts(ws)
    per = {x["value"]: {**x, "instances": 0, "seconds": 0.0} for x in ws.settings.labels}
    total = 0.0
    for _row, label, a, b in found:
        fps = float(label["fps"])
        total += (b - a + 1) / fps
        for s in clip_segments(label, a, b):
            if s["label"] in per:
                per[s["label"]]["instances"] += 1
                per[s["label"]]["seconds"] += (s["end_frame"] - s["start_frame"] + 1) / fps
    return {
        "seconds": round(total, 1),
        "videos": len({row["video_id"] for row, *_ in found}),
        "clips": len(found),
        "dropped_short_parts": short,
        "window_s": CONFIGS[ws.settings.window_config].length_s,
        "labels": [{k: (round(v, 1) if k == "seconds" else v) for k, v in x.items()} for x in per.values()],
    }


def export(ws: Workspace, name: str | None = None) -> dict:
    cfg = CONFIGS[ws.settings.window_config]
    label_names = {str(k): v for k, v in ws.label_names().items()}
    found, dropped_short = parts(ws)
    if not found:
        raise NothingToExport("No watched time yet. Watch and label parts of a video first.")
    now = datetime.now(timezone.utc)
    name = name or f"{ws.settings.labeler or 'labeler'}_{now.strftime('%Y%m%d-%H%M%S')}"
    root = ws.exports_dir / name
    (root / "videos").mkdir(parents=True, exist_ok=False)
    (root / "labels").mkdir()
    (root / "manifests").mkdir()

    video_rows, segment_rows, used = [], [], {}
    for row, label, a, b in found:
        used[row["video_id"]] = label
        fps = float(label["fps"])
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
            "parent_n_frames": row["n_frames"],
            "parent_duration_s": row["duration_s"],
            "fps": fps,
            "n_frames": n_frames,
            "duration_s": round(n_frames / fps, 6),
            "labeler": label["labeler"],
            "audit": False,
            "labeled_at": label["updated_at"],
            "label_names": label_names,
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
            "parent_n_frames": row["n_frames"],
            "parent_duration_s": row["duration_s"],
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
                "source": "staff",
                "origin": s["origin"],
                "tags": ";".join(s.get("tags", [])),
            })

    _write_csv(root / "manifests" / "videos.csv", video_rows, [
        "video_id", "source_file", "sha256", "fps", "n_frames", "duration_s", "width", "height",
        "group_id", "label_type", "parent_video_id", "parent_sha256", "clip_start_s",
        "parent_n_frames", "parent_duration_s", "audit", "labeler",
    ])
    _write_csv(root / "manifests" / "segments.csv", segment_rows, [
        "video_id", "start_s", "end_s", "start_frame", "end_frame", "label", "source", "origin", "tags",
    ])
    runs = {lab.get("prelabel_run") or "" for lab in used.values()} - {""}
    fps_all = sorted({float(lab["fps"]) for lab in used.values()})
    info = {
        "name": name,
        "path": str(root),
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_version": __version__,
        "ffmpeg": ffmpeg.version(),
        "labeler": ws.settings.labeler,
        "window_config": cfg.name,
        "videos": len(used),
        "clips": len(video_rows),
        "dropped_short_parts": dropped_short,
        "label_names": label_names,
        "counts": summarize(list(used.values()), cfg, ws.label_names()),
        "prelabel_runs": sorted(runs),
        "prompt_versions": sorted(_prompt_versions(ws, used)),
        "encode": {**ENCODE, "fps": fps_all[0] if len(fps_all) == 1 else fps_all,
                   "normalize_max_height": ws.settings.normalize_max_height},
    }
    write_json_atomic(root / "export.json", info)
    return info


def _prompt_versions(ws: Workspace, used: dict[str, dict]) -> set[str]:
    """The prompt versions of the pre-label runs that the exported videos used."""
    out = set()
    for vid, lab in used.items():
        if lab.get("prelabel_run"):
            try:
                pre = json.loads(ws.prelabel_path(lab["prelabel_run"], vid).read_text())
            except (OSError, ValueError):
                continue
            if pre.get("prompt_version"):
                out.add(pre["prompt_version"])
    return out


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
