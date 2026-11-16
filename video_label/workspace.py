"""The workspace: app settings, the video state database, and the data directories."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path.home() / "video-labeling"
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

# Steps of the processing job, in order. A video's `step` is the last step that finished.
STEPS = ["scanned", "hashed", "probed", "normalized", "frames", "prelabeled"]

# The labels of a new workspace (Setup page). The value goes into the label files and the export:
# 0 is the background (watched time with no segment) and -1 is ignore; both always exist.
# Event labels have values from 1. The key is the keyboard shortcut on the labeling screen,
# and the color is the color of the label on the labeling screen.
SEED_LABELS = [
    {"value": 1, "name": "phone", "key": "1", "color": "#d9480f"},
    {"value": 0, "name": "driving", "key": "0", "color": "#2f9e44"},
    {"value": -1, "name": "ignore", "key": "u", "color": "#868e96"},
]
# Colors for new labels, in order: a new label takes the first one that no label uses.
LABEL_PALETTE = [
    "#d9480f", "#7048e8", "#c2255c", "#1971c2", "#f08c00",
    "#9c36b5", "#0c8599", "#a61e4d", "#5c940d", "#495057",
]


def default_key(value: int) -> str:
    """The key of a label from a config.json written before labels had keys."""
    return "u" if value == -1 else str(value) if 0 <= value <= 9 else ""


def fill_label_defaults(labels: list[dict]) -> None:
    """Give keys and colors to labels from a config.json written before labels had them."""
    seed = {x["value"]: x["color"] for x in SEED_LABELS if x["value"] < 1}
    used = {x["color"] for x in labels if x.get("color")}
    for x in labels:
        x.setdefault("key", default_key(int(x["value"])))
        if not x.get("color"):
            x["color"] = seed.get(x["value"]) or next((c for c in LABEL_PALETTE if c not in used), LABEL_PALETTE[0])
            used.add(x["color"])


@dataclass
class Settings:
    labeler: str = ""
    libraries: list[str] = field(default_factory=list)
    grok_model: str = "grok-4"
    grok_base_url: str = "https://api.x.ai/v1"
    prompt_version: str = "prelabel-v1"
    normalize_fps: int = 15
    normalize_max_height: int = 720
    sample_fps: float = 2.0
    sample_height: int = 768
    chunk_s: float = 30.0
    chunk_overlap_s: float = 4.0
    grok_parallel: int = 2
    delete_frames_after_prelabel: bool = True
    window_config: str = "causal-2s"
    labels: list[dict] = field(default_factory=lambda: [dict(x) for x in SEED_LABELS])


SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  video_id     TEXT PRIMARY KEY,
  sha256       TEXT NOT NULL,
  library      TEXT NOT NULL,
  rel_path     TEXT NOT NULL,
  size         INTEGER NOT NULL,
  step         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',   -- pending | running | ok | error
  error        TEXT,
  src_fps      REAL, src_width INTEGER, src_height INTEGER, src_codec TEXT,
  duration_s   REAL, fps REAL, n_frames INTEGER, width INTEGER, height INTEGER,
  prelabel_run TEXT,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS files (
  library  TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  size     INTEGER NOT NULL,
  mtime    REAL NOT NULL,
  video_id TEXT,
  PRIMARY KEY (library, rel_path)
);
"""


class Workspace:
    def __init__(self, root: Path | str | None = None):
        root = root or os.environ.get("VIDEO_LABEL_WORKSPACE") or DEFAULT_ROOT
        self.root = Path(root).expanduser().resolve()
        for d in (self.videos_dir, self.frames_dir, self.prelabels_dir, self.labels_dir, self.exports_dir,
                  self.manual_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.root / "state.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self.settings = self._load_settings()

    # --- paths ---
    videos_dir = property(lambda self: self.root / "videos")
    frames_dir = property(lambda self: self.root / "frames")
    prelabels_dir = property(lambda self: self.root / "prelabels")
    labels_dir = property(lambda self: self.root / "labels")
    exports_dir = property(lambda self: self.root / "exports")
    manual_dir = property(lambda self: self.root / "manual-prelabel")

    def video_path(self, video_id: str) -> Path:
        return self.videos_dir / f"{video_id}.mp4"

    def label_path(self, video_id: str) -> Path:
        return self.labels_dir / f"{video_id}.json"

    def prelabel_path(self, run_id: str, video_id: str) -> Path:
        return self.prelabels_dir / run_id / f"{video_id}.json"

    # --- settings ---
    def _load_settings(self) -> Settings:
        path = self.root / "config.json"
        if not path.exists():
            return Settings()
        data = json.loads(path.read_text())
        known = Settings.__dataclass_fields__
        settings = Settings(**{k: v for k, v in data.items() if k in known})
        fill_label_defaults(settings.labels)
        return settings

    def label_names(self) -> dict[int, str]:
        return {int(x["value"]): x["name"] for x in self.settings.labels}

    def save_settings(self) -> None:
        write_json_atomic(self.root / "config.json", asdict(self.settings))

    # --- database ---
    def execute(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, params)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    def video(self, video_id: str) -> sqlite3.Row | None:
        rows = self.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,))
        return rows[0] if rows else None

    def videos(self) -> list[sqlite3.Row]:
        return self.execute("SELECT * FROM videos ORDER BY library, rel_path")

    def update_video(self, video_id: str, **fields) -> None:
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        self.execute(
            f"UPDATE videos SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE video_id = :video_id",
            {**fields, "video_id": video_id},
        )

    def source_path(self, row: sqlite3.Row) -> Path:
        return Path(row["library"]) / row["rel_path"]


def write_json_atomic(path: Path, data) -> None:
    """Write JSON to a temp file in the same directory, fsync it, then rename it over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
