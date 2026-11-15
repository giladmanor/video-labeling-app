"""Local HTTP server: the setup, jobs, and labeling pages and their JSON API."""

from __future__ import annotations

import json
import subprocess
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, export, ffmpeg, grok, store, windows
from .jobs import Worker
from .prelabel import TAGS
from .workspace import Workspace

EDITABLE_SETTINGS = {"labeler", "grok_model", "sample_fps", "chunk_s", "chunk_overlap_s", "grok_parallel"}


def create_app(ws: Workspace, start_worker: bool = True) -> FastAPI:
    worker = Worker(ws)

    @asynccontextmanager
    async def lifespan(_app):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="video-label", version=__version__, lifespan=lifespan)
    static_dir = resources.files("video_label").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def require_app_header(request: Request, call_next):
        # Other web sites can send simple POST requests to localhost. A custom header needs
        # a CORS preflight, which this server does not allow, so only our page can change data.
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            if request.headers.get("x-video-label") != "1":
                return JSONResponse({"detail": "missing X-Video-Label header"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return static_dir.joinpath("index.html").read_text()

    # --- setup ---

    @app.get("/api/state")
    def state():
        try:
            ff = ffmpeg.version()
        except Exception as e:
            ff = f"error: {e}"
        return {
            "version": __version__,
            "workspace": str(ws.root),
            "settings": ws.settings.__dict__,
            "key_present": bool(grok.get_key()),
            "ffmpeg": ff,
            "worker": {"message": worker.message, "current": worker.current},
            "tags": TAGS,
        }

    @app.post("/api/settings")
    def set_settings(data: dict = Body(...)):
        for k, v in data.items():
            if k not in EDITABLE_SETTINGS:
                raise HTTPException(400, f"setting {k} cannot be changed here")
            setattr(ws.settings, k, type(getattr(ws.settings, k))(v))
        ws.save_settings()
        return ws.settings.__dict__

    @app.post("/api/libraries")
    def add_library(data: dict = Body(...)):
        path = Path(str(data.get("path", "")).strip()).expanduser()
        if not path.is_dir():
            raise HTTPException(400, f"not a directory, or no access: {path}")
        path = str(path.resolve())
        if path not in ws.settings.libraries:
            ws.settings.libraries.append(path)
            ws.save_settings()
        worker.wake()
        return ws.settings.libraries

    @app.post("/api/libraries/remove")
    def remove_library(data: dict = Body(...)):
        path = data.get("path")
        if path in ws.settings.libraries:
            ws.settings.libraries.remove(path)
            ws.save_settings()
        return ws.settings.libraries

    @app.post("/api/libraries/choose")
    def choose_library():
        """Open the macOS folder dialog. A browser page cannot get a real folder path."""
        proc = subprocess.run(
            ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select a video library")'],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return {"path": None}  # the user pressed Cancel
        return {"path": proc.stdout.strip().rstrip("/") or "/"}

    @app.post("/api/grok/key")
    def set_key(data: dict = Body(...)):
        key = str(data.get("key", "")).strip()
        if not key:
            raise HTTPException(400, "empty key")
        client = grok.GrokClient(key, ws.settings.grok_base_url, ws.settings.grok_model)
        try:
            models = client.models()
        except grok.GrokError as e:
            raise HTTPException(400, f"the key does not work: {e}")
        finally:
            client.close()
        grok.set_key(key)
        worker.wake()
        return {"models": models}

    @app.get("/api/grok/models")
    def grok_models():
        key = grok.get_key()
        if not key:
            raise HTTPException(400, "no API key")
        client = grok.GrokClient(key, ws.settings.grok_base_url, ws.settings.grok_model)
        try:
            return {"models": client.models()}
        except grok.GrokError as e:
            raise HTTPException(502, str(e))
        finally:
            client.close()

    # --- jobs and videos ---

    def _label_info(video_id: str) -> dict:
        label = store.load(ws.label_path(video_id))
        if not label:
            return {"label_status": None, "watched_fraction": 0.0}
        watched = sum(b - a + 1 for a, b in label["watched"])
        return {"label_status": label["status"], "watched_fraction": round(watched / max(1, label["n_frames"]), 3)}

    @app.get("/api/videos")
    def videos():
        out = []
        for r in ws.videos():
            n_candidates = None
            if r["prelabel_run"]:
                p = ws.prelabel_path(r["prelabel_run"], r["video_id"])
                if p.exists():
                    n_candidates = len(json.loads(p.read_text())["candidates"])
            out.append({
                "video_id": r["video_id"], "file": r["rel_path"], "library": r["library"],
                "step": r["step"], "status": r["status"], "error": r["error"],
                "duration_s": r["duration_s"], "candidates": n_candidates, **_label_info(r["video_id"]),
            })
        return out

    @app.post("/api/jobs/run")
    def run_jobs():
        ws.execute("UPDATE videos SET status = 'pending' WHERE status = 'error'")
        worker.wake()
        return {"ok": True}

    @app.get("/media/{video_id}.mp4")
    def media(video_id: str):
        if ws.video(video_id) is None or not ws.video_path(video_id).exists():
            raise HTTPException(404)
        return FileResponse(ws.video_path(video_id), media_type="video/mp4")

    # --- labeling ---

    def _get_label(row) -> dict:
        label = store.load(ws.label_path(row["video_id"]))
        if label is None:
            label = store.new_label(row["video_id"], ws.settings.labeler, row["fps"], row["n_frames"], row["prelabel_run"])
        return label

    @app.get("/api/videos/{video_id}")
    def video(video_id: str):
        row = ws.video(video_id)
        if row is None:
            raise HTTPException(404)
        if row["step"] not in ("normalized", "frames", "prelabeled"):
            raise HTTPException(409, "the video is not processed yet")
        pre = None
        if row["prelabel_run"]:
            p = ws.prelabel_path(row["prelabel_run"], video_id)
            pre = json.loads(p.read_text()) if p.exists() else None
        return {
            "video": {k: row[k] for k in row.keys()},
            "media_url": f"/media/{video_id}.mp4",
            "prelabel": pre,
            "label": _get_label(row),
        }

    @app.put("/api/labels/{video_id}")
    def put_label(video_id: str, label: dict = Body(...)):
        row = ws.video(video_id)
        if row is None or label.get("video_id") != video_id:
            raise HTTPException(404)
        if label.get("n_frames") != row["n_frames"]:
            raise HTTPException(400, "n_frames does not match the video")
        if not ws.settings.labeler:
            raise HTTPException(409, "set your labeler name on the Setup page first")
        label["labeler"] = label.get("labeler") or ws.settings.labeler
        try:
            return store.save(ws.label_path(video_id), label)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))

    @app.post("/api/export")
    def run_export():
        try:
            return export.export(ws)
        except export.NothingToExport as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(500, f"export failed: {e}")

    @app.post("/api/export/reveal")
    def reveal_export(data: dict = Body(...)):
        """Show an export folder in the Finder."""
        path = Path(str(data.get("path", ""))).resolve()
        if ws.exports_dir.resolve() not in path.parents or not path.is_dir():
            raise HTTPException(400, "not an export folder")
        subprocess.run(["open", str(path)], check=False)
        return {"ok": True}

    @app.get("/api/summary")
    def summary(video_id: str | None = None):
        cfg = windows.CONFIGS[ws.settings.window_config]
        done, current = [], None
        for p in sorted(ws.labels_dir.glob("*.json")):
            label = store.load(p)
            if label["video_id"] == video_id:
                current = label
            if label["status"] == "done":
                done.append(label)
        if current is not None and current["status"] != "done":
            all_labels = done + [current]
        else:
            all_labels = done
        return {
            "all": windows.summarize(all_labels, cfg),
            "video": windows.summarize([current], cfg) if current else None,
        }

    return app
