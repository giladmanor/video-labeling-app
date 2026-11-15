"""End to end: a generated video goes through the jobs with a fake Grok, then the API."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_label import ffmpeg, jobs, store
from video_label.server import create_app
from video_label.workspace import Workspace


class FakeGrok:
    model = "fake-model"

    def __init__(self):
        self.calls = []

    def chat(self, messages):
        user = messages[1]["content"]
        times = [float(c["text"][2:]) for c in user if c["type"] == "text"]
        self.calls.append(times)
        cands = []
        if times[0] <= 3.0 <= times[-1]:
            cands.append({"start_s": 3.0, "end_s": 4.5, "confidence": 0.8, "tags": [],
                          "cut_at_start": False, "cut_at_end": False, "evidence": "phone in right hand"})
        return "```json\n" + json.dumps({"candidates": cands, "unusable": []}) + "\n```", {"total_tokens": 100}

    def close(self):
        pass


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    (lib / "day1").mkdir(parents=True)
    ff, _ = ffmpeg.binaries()
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(lib / "day1" / "clip one.mp4")], check=True)
    (lib / ".hidden.mp4").write_bytes(b"x")
    (lib / "notes.txt").write_text("not a video")
    return lib


def test_jobs_and_api(tmp_path, library):
    ws = Workspace(tmp_path / "ws")
    ws.settings.libraries = [str(library)]
    ws.settings.chunk_s = 5.0
    ws.settings.chunk_overlap_s = 1.0

    assert jobs.scan(ws) == 1
    assert jobs.scan(ws) == 0   # unchanged files are not hashed again
    (video_id,) = [r["video_id"] for r in ws.videos()]

    fake = FakeGrok()
    jobs.process_video(ws, video_id, fake)
    row = ws.video(video_id)
    assert row["step"] == "prelabeled" and row["status"] == "ok"
    assert row["fps"] == 15 and row["n_frames"] == 120 and row["height"] == 240
    assert sorted(c[0] for c in fake.calls) == [0.0, 4.0]   # chunks run in parallel
    assert not (ws.frames_dir / video_id).exists()   # frames deleted after a full pre-label

    pre = json.loads(ws.prelabel_path(row["prelabel_run"], video_id).read_text())
    assert [(c["id"], c["start_s"], c["end_s"]) for c in pre["candidates"]] == [("c001", 3.0, 4.5)]
    assert pre["covered"] == [[0.0, 7.5]] and pre["failed_chunks"] == []

    client = TestClient(create_app(ws, start_worker=False))
    data = client.get(f"/api/videos/{video_id}").json()
    label = data["label"]
    assert label["status"] == "todo" and label["n_frames"] == 120

    r = client.get(data["media_url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    r = client.post("/api/export", headers={"X-Video-Label": "1"})
    assert r.status_code == 400 and "No video is marked done" in r.json()["detail"]

    label["segments"] = [{"start_frame": 45, "end_frame": 67, "label": 1, "origin": "grok_accepted",
                          "tags": [], "candidate_id": "c001", "candidate_confidence": 0.8}]
    label["watched"] = [[0, 119]]
    label["decisions"] = {"c001": "accepted"}
    label["status"] = "done"
    assert client.put(f"/api/labels/{video_id}", json=label).status_code == 403   # no app header
    r = client.put(f"/api/labels/{video_id}", json=label, headers={"X-Video-Label": "1"})
    assert r.status_code == 409   # no labeler name yet
    ws.settings.labeler = "tester"
    r = client.put(f"/api/labels/{video_id}", json=label, headers={"X-Video-Label": "1"})
    assert r.status_code == 200, r.text

    saved = store.load(ws.label_path(video_id))
    assert saved["decisions"] == {"c001": "accepted"} and saved["labeler"] == "tester"

    s = client.get(f"/api/summary?video_id={video_id}").json()["all"]
    assert s["source_videos"] == 1 and s["phone_events"] == 1
    assert s["positive_windows"] > 0 and s["negative_windows"] > 0

    info = client.post("/api/export", headers={"X-Video-Label": "1"}).json()
    assert info["clips"] == 1
    assert info["clips"] == 1 and info["done_videos"] == 1
    root = Path(info["path"])
    clip_id = f"{video_id}_000000"
    clip = ffmpeg.probe(root / "videos" / f"{clip_id}.mp4", count_frames=True)
    assert clip.n_frames == 120 and clip.fps == 15
    lab = json.loads((root / "labels" / f"{clip_id}.json").read_text())
    assert [(s["start_frame"], s["end_frame"], s["label"]) for s in lab["segments"]] == [(0, 44, 0), (45, 67, 1), (68, 119, 0)]
    seg_csv = (root / "manifests" / "segments.csv").read_text().splitlines()
    assert seg_csv[0].startswith("video_id,start_s,end_s") and len(seg_csv) == 4
    assert "group_id" in (root / "manifests" / "videos.csv").read_text()

    bad = dict(label, segments=label["segments"] * 2)
    assert client.put(f"/api/labels/{video_id}", json=bad, headers={"X-Video-Label": "1"}).status_code == 400


def test_waiting_for_key(tmp_path, library):
    ws = Workspace(tmp_path / "ws")
    ws.settings.libraries = [str(library)]
    jobs.scan(ws)
    (video_id,) = [r["video_id"] for r in ws.videos()]
    jobs.process_video(ws, video_id, None)
    row = ws.video(video_id)
    assert row["step"] == "frames" and row["status"] == "waiting_key"
    assert any((ws.frames_dir / video_id).glob("*.jpg"))
