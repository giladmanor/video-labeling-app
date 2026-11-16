"""Local HTTP server: the setup, jobs, and labeling pages and their JSON API."""

from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, export, ffmpeg, manual, store, windows
from .jobs import Worker
from .prelabel import TAGS
from .workspace import LABEL_PALETTE, Workspace

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
EDITABLE_SETTINGS = {"labeler", "sample_fps", "chunk_s", "chunk_overlap_s"}


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
    # DNS rebinding: a web site can point its own name at 127.0.0.1 and then read this server
    # as the same origin (also with the custom header). Only accept our own host names.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.middleware("http")
    async def require_app_header(request: Request, call_next):
        # Other web sites can send simple POST requests to localhost. A custom header needs
        # a CORS preflight, which this server does not allow, so only our page can change data.
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            if request.headers.get("x-video-label") != "1":
                return JSONResponse({"detail": "missing X-Video-Label header"}, status_code=403)
        response = await call_next(request)
        # The page, app.js, and style.css change with the app version: the browser must ask each
        # time (a 304 when unchanged) and never mix a cached old file with a new one.
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def _versioned(html: str) -> str:
        # app.js?v=<content hash>: a browser never uses a cached copy of an older version.
        for name in ("app.js", "style.css"):
            v = hashlib.sha256(static_dir.joinpath(name).read_bytes()).hexdigest()[:12]
            html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
        return html

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _versioned(static_dir.joinpath("index.html").read_text())

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
            "ffmpeg": ff,
            "worker": {"message": worker.message, "current": worker.current},
            "tags": TAGS,
            "reserved_keys": "".join(sorted(store.RESERVED_KEYS)),
            "label_palette": LABEL_PALETTE,
        }

    @app.post("/api/settings")
    def set_settings(data: dict = Body(...)):
        for k, v in data.items():
            if k not in EDITABLE_SETTINGS:
                raise HTTPException(400, f"setting {k} cannot be changed here")
            setattr(ws.settings, k, type(getattr(ws.settings, k))(v))
        ws.save_settings()
        return ws.settings.__dict__

    @app.post("/api/settings/labels")
    def set_labels(data: dict = Body(...)):
        try:
            labels = store.check_label_config(data.get("labels") or [])
        except ValueError as e:
            raise HTTPException(400, str(e))
        removed = set(ws.label_names()) - {x["value"] for x in labels}
        in_use = removed & store.used_values(store.load(p) for p in ws.labels_dir.glob("*.json"))
        if in_use:
            names = ", ".join(ws.label_names()[v] for v in sorted(in_use))
            raise HTTPException(409, f"labels in use in label files cannot be removed: {names}")
        ws.settings.labels = labels
        ws.save_settings()
        return labels

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
                "duration_s": r["duration_s"], "candidates": n_candidates, "prelabel_run": r["prelabel_run"], **_label_info(r["video_id"]),
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
            return store.save(ws.label_path(video_id), label, set(ws.label_names()))
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))

    @app.get("/api/export/stats")
    def export_stats():
        """The watched time that an export would hold now (the Videos page)."""
        return export.stats(ws)

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
        """Show an export or manual pre-label folder in the Finder."""
        path = Path(str(data.get("path", ""))).resolve()
        roots = (ws.exports_dir.resolve(), ws.manual_dir.resolve())
        if not any(r in path.parents for r in roots) or not path.is_dir():
            raise HTTPException(400, "not an export folder")
        subprocess.run(["open", str(path)], check=False)
        return {"ok": True}

    # --- manual pre-labeling (a directory of frames and a README.md for a labeling LLM) ---

    @app.get("/api/manual")
    def manual_sets(video_id: str | None = None):
        return {"defaults": manual.defaults(ws), "sets": manual.list_sets(ws, video_id)}

    @app.post("/api/manual/prepare")
    def manual_prepare(data: dict = Body(...)):
        try:
            return manual.prepare(
                ws, str(data.get("video_id", "")), sample_fps=float(data["sample_fps"]),
                height=int(data["height"]), readme=str(data.get("readme", "")),
            )
        except (KeyError, TypeError, ValueError) as e:   # ManualError is a ValueError
            raise HTTPException(400, str(e))
        except ffmpeg.FFmpegError as e:
            raise HTTPException(500, str(e))

    @app.get("/api/manual/{name}")
    def manual_set(name: str):
        try:
            return manual.describe(ws, name)
        except manual.ManualError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/manual/{name}/import")
    def manual_import(name: str, data: dict = Body(...)):
        try:
            return manual.import_set(ws, name, str(data.get("model", "")))
        except manual.ManualError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/summary")
    def summary(video_id: str | None = None):
        cfg = windows.CONFIGS[ws.settings.window_config]
        # All watched time counts, as in the export; skipped videos do not.
        all_labels, current = [], None
        for p in sorted(ws.labels_dir.glob("*.json")):
            label = store.load(p)
            if label["video_id"] == video_id:
                current = label
            if label["status"] != "skip":
                all_labels.append(label)
        return {
            "all": windows.summarize(all_labels, cfg, ws.label_names()),
            "video": windows.summarize([current], cfg, ws.label_names()) if current else None,
        }

    return app
