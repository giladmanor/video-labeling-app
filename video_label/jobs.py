"""Processing jobs: scan the libraries, hash, probe, normalize, extract frames, pre-label.

One background worker runs the steps video by video (docs/design.md).
The state is in the workspace database, so the jobs continue after a restart.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from pathlib import Path

from . import ffmpeg, grok, prelabel
from .workspace import STEPS, VIDEO_EXTS, Workspace, write_json_atomic

log = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scan(ws: Workspace) -> int:
    """Find video files in the libraries and hash the new or changed ones. Returns the new video count."""
    new = 0
    for lib in ws.settings.libraries:
        root = Path(lib)
        if not root.is_dir():
            log.warning("library not found or no access: %s", root)
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(part.startswith(".") for part in rel.parts) or path.suffix.lower() not in VIDEO_EXTS:
                continue
            if not path.is_file():
                continue
            st = path.stat()
            known = ws.execute(
                "SELECT size, mtime, video_id FROM files WHERE library = ? AND rel_path = ?", (lib, str(rel))
            )
            if known and known[0]["size"] == st.st_size and known[0]["mtime"] == st.st_mtime and known[0]["video_id"]:
                continue
            digest = sha256_file(path)
            video_id = digest[:12]
            ws.execute(
                "INSERT OR REPLACE INTO files (library, rel_path, size, mtime, video_id) VALUES (?, ?, ?, ?, ?)",
                (lib, str(rel), st.st_size, st.st_mtime, video_id),
            )
            if ws.video(video_id) is None:
                ws.execute(
                    "INSERT INTO videos (video_id, sha256, library, rel_path, size, step) VALUES (?, ?, ?, ?, ?, 'hashed')",
                    (video_id, digest, lib, str(rel), st.st_size),
                )
                new += 1
    return new


def _done(row, step: str) -> bool:
    return STEPS.index(row["step"]) >= STEPS.index(step)


def process_video(ws: Workspace, video_id: str, client: grok.GrokClient | None) -> None:
    """Run the missing steps of one video. Raises on error."""
    s = ws.settings
    row = ws.video(video_id)
    src = ws.source_path(row)
    if not _done(row, "probed"):
        p = ffmpeg.probe(src)
        ws.update_video(video_id, step="probed", src_fps=p.fps, src_width=p.width,
                        src_height=p.height, src_codec=p.codec, duration_s=p.duration_s)
        row = ws.video(video_id)
    if not _done(row, "normalized"):
        dst = ws.video_path(video_id)
        ffmpeg.normalize(src, dst, s.normalize_fps, s.normalize_max_height)
        p = ffmpeg.probe(dst, count_frames=True)
        ws.update_video(video_id, step="normalized", fps=p.fps, n_frames=p.n_frames,
                        width=p.width, height=p.height, duration_s=p.duration_s)
        row = ws.video(video_id)
    frames_dir = ws.frames_dir / video_id
    if not _done(row, "frames") or (not _done(row, "prelabeled") and not frames_dir.exists()):
        ffmpeg.extract_frames(ws.video_path(video_id), frames_dir, s.sample_fps, s.sample_height)
        ws.update_video(video_id, step="frames")
        row = ws.video(video_id)
    if not _done(row, "prelabeled"):
        if client is None:
            ws.update_video(video_id, status="waiting_key")
            return
        result = prelabel.prelabel_video(
            client, video_id=video_id, frames=sorted(frames_dir.glob("*.jpg")),
            duration_s=row["duration_s"], sample_fps=s.sample_fps, chunk_s=s.chunk_s,
            overlap_s=s.chunk_overlap_s, prompt_version=s.prompt_version, parallel=s.grok_parallel,
        )
        if not result["covered"]:
            errors = "; ".join(c["error"] for c in result["failed_chunks"][:2])
            raise RuntimeError(f"all Grok chunks failed: {errors}")
        write_json_atomic(ws.prelabel_path(result["run_id"], video_id), result)
        ws.update_video(video_id, step="prelabeled", prelabel_run=result["run_id"])
        if s.delete_frames_after_prelabel and not result["failed_chunks"]:
            shutil.rmtree(frames_dir, ignore_errors=True)
    ws.update_video(video_id, status="ok", error=None)


class Worker:
    """Background thread that scans the libraries and processes the videos one by one."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.current: str | None = None
        self.message = "idle"
        self._thread = threading.Thread(target=self._loop, name="jobs", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _client(self) -> grok.GrokClient | None:
        key = grok.get_key()
        if not key:
            return None
        return grok.GrokClient(key, self.ws.settings.grok_base_url, self.ws.settings.grok_model)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            try:
                self.message = "scanning libraries"
                scan(self.ws)
                # Videos that were running when the app stopped, or waited for a key.
                self.ws.execute("UPDATE videos SET status = 'pending' WHERE status IN ('running', 'waiting_key')")
                client = self._client()
                todo = self.ws.execute(
                    "SELECT video_id FROM videos WHERE status = 'pending' ORDER BY library, rel_path"
                )
                for r in todo:
                    if self._stop.is_set():
                        break
                    vid = r["video_id"]
                    self.current = vid
                    self.message = f"processing {vid}"
                    self.ws.update_video(vid, status="running")
                    try:
                        process_video(self.ws, vid, client)
                    except Exception as e:  # one bad video must not stop the others
                        log.exception("video %s failed", vid)
                        self.ws.update_video(vid, status="error", error=str(e)[:500])
                if client:
                    client.close()
            except Exception:
                log.exception("job loop failed")
            finally:
                self.current = None
                self.message = "idle"
