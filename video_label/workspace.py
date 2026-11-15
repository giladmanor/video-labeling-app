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
        for d in (self.videos_dir, self.frames_dir, self.prelabels_dir, self.labels_dir, self.exports_dir):
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
        return Settings(**{k: v for k, v in data.items() if k in known})

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
