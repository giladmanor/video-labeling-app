"""Manual pre-labeling: a directory of frames and instructions for a labeling LLM used by hand.

For a model with no API connection, for example a chat or agent session that can read local
files (docs/prelabel-prompt.md). The app writes all the frames of a video into one directory,
with a README.md that tells the model how to look at every frame at its own pace and how to
write the result. The person pastes a short instruction into the model session; the model
writes prelabels.json in the directory, and the app imports it as a pre-label run, with the
same check and merge as the Grok step.

Layout: manual-prelabel/{video_id}_{date-time}/
  README.md  manifest.json  frame_00001_t0000.0s.jpg ...  prelabels.json (written by the model)
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from string import Template

from . import ffmpeg, prelabel
from .workspace import STEPS, Workspace, write_json_atomic

SET_RE = re.compile(r"^[0-9a-f]{12}_\d{8}-\d{6}$")
LAYOUT = "directory"   # sets of an older version (chunk folders) have no layout and are not listed
PROMPT_VERSION = "prelabel-dir-v1"
ANSWER_FILE = "prelabels.json"


class ManualError(ValueError):
    pass


def load_template(version: str = PROMPT_VERSION) -> str:
    return resources.files("video_label").joinpath("prompts", f"{version}.md").read_text()


def defaults(ws: Workspace) -> dict:
    return {
        "readme": load_template(),
        "prompt_version": PROMPT_VERSION,
        "sample_fps": ws.settings.sample_fps,
        "height": ws.settings.sample_height,
        "placeholders": ["$video_id", "$n_images", "$interval_s", "$duration_s", "$first_s", "$last_s", "$answer_file"],
    }


def paste_instruction(path: Path) -> str:
    """The short instruction that the person pastes into the labeling model session."""
    return (
        f"Pre-label the video frames in the directory {path}\n"
        f"Read {path / 'README.md'} first and follow it exactly: look at every image in order, "
        f"at your own pace, and then write the result as {path / ANSWER_FILE} "
        "in the JSON structure that the README gives."
    )


def _set_dir(ws: Workspace, name: str) -> Path:
    if not SET_RE.match(name) or not (ws.manual_dir / name / "manifest.json").exists():
        raise ManualError(f"no prepared set {name!r}")
    return ws.manual_dir / name


def prepare(ws: Workspace, video_id: str, *, sample_fps: float, height: int, readme: str) -> dict:
    """Extract the frames into one directory and write the README.md for the model."""
    row = ws.video(video_id)
    if row is None or STEPS.index(row["step"]) < STEPS.index("normalized"):
        raise ManualError("the video is not processed yet")
    if not (0 < sample_fps <= 10 and 64 <= height <= 2160):
        raise ManualError("check the numbers: 0 < frames per second <= 10, 64 <= height <= 2160")
    if not readme.strip():
        raise ManualError("the instruction is empty")

    now = datetime.now(timezone.utc)
    name = f"{video_id}_{now.strftime('%Y%m%d-%H%M%S')}"
    root = ws.manual_dir / name
    if root.exists():
        raise ManualError("a set was prepared for this video one second ago; try again")
    frames = ffmpeg.extract_frames(ws.video_path(video_id), root / ".frames", sample_fps, height)
    if not frames:
        shutil.rmtree(root, ignore_errors=True)
        raise ManualError("ffmpeg extracted no frames")
    times = []
    for i, src in enumerate(frames):
        t = prelabel.frame_time(i, sample_fps)
        src.replace(root / f"frame_{i + 1:05d}_t{t:06.1f}s.jpg")
        times.append(t)
    shutil.rmtree(root / ".frames")

    values = {
        "video_id": video_id, "n_images": len(times), "interval_s": f"{1 / sample_fps:g}",
        "duration_s": f"{row['duration_s']:.1f}", "first_s": f"{times[0]:.1f}", "last_s": f"{times[-1]:.1f}",
        "answer_file": ANSWER_FILE,
    }
    (root / "README.md").write_text(Template(readme).safe_substitute(values).rstrip() + "\n")
    version = PROMPT_VERSION if readme.strip() == load_template().strip() else f"{PROMPT_VERSION}-custom"
    write_json_atomic(root / "manifest.json", {
        "layout": LAYOUT,
        "name": name,
        "video_id": video_id,
        "file": row["rel_path"],
        "duration_s": row["duration_s"],
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt_version": version,
        "sample_fps": sample_fps,
        "height": height,
        "n_images": len(times),
        "first_s": times[0],
        "last_s": times[-1],
    })
    return describe(ws, name)


def read_answer(root: Path, m: dict) -> dict:
    """Read and check prelabels.json. Returns {"answer": ..., "model": ...}; raises ValueError."""
    path = root / ANSWER_FILE
    if not path.exists():
        raise FileNotFoundError(ANSWER_FILE)
    obj = prelabel.parse_answer(path.read_text())
    if not isinstance(obj, dict):
        raise ValueError("the file must contain one JSON object")
    if obj.get("video_id", m["video_id"]) != m["video_id"]:
        raise ValueError(f"video_id {obj.get('video_id')!r} is not {m['video_id']!r}")
    model = str(obj.get("model") or "").strip()
    core = {k: v for k, v in obj.items() if k not in ("video_id", "model")}
    return {"answer": prelabel.check_answer(core, m["first_s"], m["last_s"]), "model": model}


def describe(ws: Workspace, name: str) -> dict:
    """The manifest with the path, the paste instruction, and the state of prelabels.json."""
    root = _set_dir(ws, name)
    m = json.loads((root / "manifest.json").read_text())
    try:
        r = read_answer(root, m)
        m["answer"] = {"ok": True, "model": r["model"], "candidates": len(r["answer"]["candidates"]),
                       "unusable": len(r["answer"]["unusable"])}
    except FileNotFoundError:
        m["answer"] = None
    except (ValueError, json.JSONDecodeError) as e:
        m["answer"] = {"ok": False, "error": str(e)}
    m["path"] = str(root)
    m["answer_path"] = str(root / ANSWER_FILE)
    m["instruction"] = paste_instruction(root)
    return m


def list_sets(ws: Workspace, video_id: str | None = None) -> list[dict]:
    out = []
    for d in sorted(ws.manual_dir.iterdir(), reverse=True):
        if not (SET_RE.match(d.name) and (d / "manifest.json").exists()):
            continue
        if video_id is not None and not d.name.startswith(f"{video_id}_"):
            continue
        if json.loads((d / "manifest.json").read_text()).get("layout") == LAYOUT:
            out.append(describe(ws, d.name))
    return out


def import_set(ws: Workspace, name: str, model: str = "") -> dict:
    """Check prelabels.json and use it as the pre-label run of the video."""
    root = _set_dir(ws, name)
    m = json.loads((root / "manifest.json").read_text())
    try:
        r = read_answer(root, m)
    except FileNotFoundError:
        raise ManualError(f"{ANSWER_FILE} is not in the directory yet") from None
    except (ValueError, json.JSONDecodeError) as e:
        raise ManualError(f"{ANSWER_FILE} is not valid: {e}") from None
    model = model.strip() or r["model"]
    if not model:
        raise ManualError("enter the model name, for example claude-opus-5-5")
    results = [{"t0": m["first_s"], "t1": m["last_s"], "usage": {}, "answer": r["answer"]}]
    result = prelabel.assemble(results, video_id=m["video_id"], model=model, prompt_version=m["prompt_version"],
                               sample_fps=m["sample_fps"], duration_s=m["duration_s"])
    write_json_atomic(ws.prelabel_path(result["run_id"], m["video_id"]), result)
    ws.update_video(m["video_id"], step="prelabeled", status="ok", error=None, prelabel_run=result["run_id"])
    # The page shows an imported set as done (its steps collapse to "again" buttons).
    m.update(imported_run=result["run_id"], imported_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    write_json_atomic(root / "manifest.json", m)
    return {"run_id": result["run_id"], "model": model, "candidates": len(result["candidates"]),
            "covered": result["covered"]}
