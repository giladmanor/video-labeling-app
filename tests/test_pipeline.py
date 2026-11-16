"""End to end: a generated video goes through the jobs with a fake Grok, then the API."""

import csv
import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_label import export, ffmpeg, jobs, store
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

    client = TestClient(create_app(ws, start_worker=False), base_url="http://127.0.0.1:8765")
    assert client.get("/api/state", headers={"Host": "evil.example:8765"}).status_code == 400   # DNS rebinding
    data = client.get(f"/api/videos/{video_id}").json()
    label = data["label"]
    assert label["status"] == "todo" and label["n_frames"] == 120

    r = client.get(data["media_url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    r = client.post("/api/export", headers={"X-Video-Label": "1"})
    assert r.status_code == 400 and "No watched time" in r.json()["detail"]

    label["segments"] = [{"start_frame": 45, "end_frame": 67, "label": 1, "origin": "grok_accepted",
                          "tags": [], "candidate_id": "c001", "candidate_confidence": 0.8}]
    label["watched"] = [[0, 119]]
    label["decisions"] = {"c001": "accepted"}   # the status stays "todo": watched time is enough
    assert client.put(f"/api/labels/{video_id}", json=label).status_code == 403   # no app header
    r = client.put(f"/api/labels/{video_id}", json=label, headers={"X-Video-Label": "1"})
    assert r.status_code == 409   # no labeler name yet
    ws.settings.labeler = "tester"
    r = client.put(f"/api/labels/{video_id}", json=label, headers={"X-Video-Label": "1"})
    assert r.status_code == 200, r.text

    saved = store.load(ws.label_path(video_id))
    assert saved["decisions"] == {"c001": "accepted"} and saved["labeler"] == "tester"

    s = client.get(f"/api/summary?video_id={video_id}").json()["all"]
    assert s["source_videos"] == 1 and s["events"] == 1
    assert s["positive_windows"] > 0 and s["negative_windows"] > 0

    st = client.get("/api/export/stats").json()
    assert st["seconds"] == 8.0 and st["videos"] == 1 and st["clips"] == 1
    by_name = {x["name"]: x for x in st["labels"]}
    assert by_name["phone"]["instances"] == 1 and by_name["phone"]["seconds"] == round(23 / 15, 1)
    assert by_name["driving"]["instances"] == 2 and by_name["ignore"]["instances"] == 0

    info = client.post("/api/export", headers={"X-Video-Label": "1"}).json()
    assert info["clips"] == 1 and info["videos"] == 1
    assert info["prompt_versions"] == ["prelabel-v1"] and info["encode"]["crf"] == 18
    root = Path(info["path"])
    clip_id = f"{video_id}_000000"
    clip = ffmpeg.probe(root / "videos" / f"{clip_id}.mp4", count_frames=True)
    assert clip.n_frames == 120 and clip.fps == 15
    lab = json.loads((root / "labels" / f"{clip_id}.json").read_text())
    assert [(s["start_frame"], s["end_frame"], s["label"]) for s in lab["segments"]] == [(0, 44, 0), (45, 67, 1), (68, 119, 0)]
    seg_csv = (root / "manifests" / "segments.csv").read_text().splitlines()
    assert seg_csv[0].startswith("video_id,start_s,end_s") and len(seg_csv) == 4
    assert all(line.split(",")[6] == "staff" for line in seg_csv[1:])
    (vrow,) = csv.DictReader((root / "manifests" / "videos.csv").open())
    assert vrow["group_id"] == video_id and vrow["parent_duration_s"] == "8.0" and vrow["parent_n_frames"] == "120"

    skipped = dict(saved, status="skip")
    assert client.put(f"/api/labels/{video_id}", json=skipped, headers={"X-Video-Label": "1"}).status_code == 200
    assert client.get("/api/export/stats").json()["clips"] == 0   # a skipped video is not exported

    bad = dict(label, segments=label["segments"] * 2)
    assert client.put(f"/api/labels/{video_id}", json=bad, headers={"X-Video-Label": "1"}).status_code == 400


def test_without_grok_stops_after_normalize(tmp_path, library):
    ws = Workspace(tmp_path / "ws")
    ws.settings.libraries = [str(library)]
    jobs.scan(ws)
    (video_id,) = [r["video_id"] for r in ws.videos()]
    jobs.process_video(ws, video_id, None)
    ws.update_video(video_id, step="frames", status="waiting_key")   # a video left by an older version
    (ws.frames_dir / video_id).mkdir()
    jobs.process_video(ws, video_id, None)
    row = ws.video(video_id)
    assert row["step"] == "normalized" and row["status"] == "ok"
    assert ws.video_path(video_id).exists() and not (ws.frames_dir / video_id).exists()
    client = TestClient(create_app(ws, start_worker=False), base_url="http://127.0.0.1")
    assert "key_present" not in client.get("/api/state").json()
    assert client.post("/api/grok/key", json={"key": "x"}, headers={"X-Video-Label": "1"}).status_code in (404, 405)


def test_label_config_api(tmp_path):
    ws = Workspace(tmp_path / "ws")
    client = TestClient(create_app(ws, start_worker=False), base_url="http://127.0.0.1")
    h = {"X-Video-Label": "1"}
    assert client.get("/api/state").json()["settings"]["labels"][0] == {
        "value": 1, "name": "phone", "key": "1", "color": "#d9480f"}
    new = [{"value": 1, "name": "phone", "key": "1", "color": "#d9480f"},
           {"value": 2, "name": "eating", "key": "e", "color": "#00ff00"},
           {"value": 0, "name": "driving", "key": "0", "color": "#2f9e44"},
           {"value": -1, "name": "ignore", "key": "u", "color": "#868e96"}]
    assert client.post("/api/settings/labels", json={"labels": new}, headers=h).status_code == 200
    reloaded = Workspace(tmp_path / "ws")   # saved in config.json
    assert reloaded.label_names()[2] == "eating" and reloaded.settings.labels[1]["color"] == "#00ff00"
    lab = store.new_label("v1", "t", 10.0, 100, None)
    lab["segments"] = [{"start_frame": 0, "end_frame": 5, "label": 2, "origin": "manual", "tags": []}]
    store.save(ws.label_path("v1"), lab, {1, 2, 0, -1})
    r = client.post("/api/settings/labels", json={"labels": [new[0]] + new[2:]}, headers=h)
    assert r.status_code == 409 and "eating" in r.json()["detail"]   # in use
    r = client.post("/api/settings/labels", json={"labels": new[:2]}, headers=h)
    assert r.status_code == 400   # 0 and -1 are required


def test_static_files_are_not_cached(tmp_path):
    ws = Workspace(tmp_path / "ws")
    client = TestClient(create_app(ws, start_worker=False), base_url="http://127.0.0.1")
    for path in ("/", "/static/app.js", "/static/style.css"):
        assert client.get(path).headers["cache-control"] == "no-cache"
    page = client.get("/").text
    assert re.search(r'/static/app\.js\?v=[0-9a-f]{12}"', page)
    assert re.search(r'/static/style\.css\?v=[0-9a-f]{12}"', page)


def test_manual_prelabel_files(tmp_path, library):
    ws = Workspace(tmp_path / "ws")
    ws.settings.libraries = [str(library)]
    jobs.scan(ws)
    (video_id,) = [r["video_id"] for r in ws.videos()]
    jobs.process_video(ws, video_id, None)   # the app stops after the playback copy
    client = TestClient(create_app(ws, start_worker=False), base_url="http://127.0.0.1")
    h = {"X-Video-Label": "1"}

    d = client.get("/api/manual").json()["defaults"]
    assert "$answer_file" in d["readme"]
    r = client.post("/api/manual/prepare", headers=h, json={
        "video_id": video_id, "sample_fps": 2, "height": 120, "readme": d["readme"]})
    assert r.status_code == 200, r.text
    s = r.json()
    root = Path(s["path"])
    assert s["n_images"] == 16 and s["first_s"] == 0.0 and s["last_s"] == 7.5
    assert s["prompt_version"] == "prelabel-dir-v1" and s["answer"] is None
    images = sorted(p.name for p in root.glob("*.jpg"))
    assert len(images) == 16 and images[0] == "frame_00001_t0000.0s.jpg" and images[-1] == "frame_00016_t0007.5s.jpg"
    readme = (root / "README.md").read_text()
    assert f"video {video_id}" in readme and "16 images" in readme and "prelabels.json" in readme and "$" not in readme
    assert str(root / "README.md") in s["instruction"] and str(root / "prelabels.json") in s["instruction"]

    r = client.post(f"/api/manual/{s['name']}/import", headers=h, json={"model": ""})
    assert r.status_code == 400 and "not in the directory" in r.json()["detail"]
    (root / "prelabels.json").write_text(json.dumps({"video_id": video_id, "model": "agent-x", "candidates": [
        {"start_s": 1.0, "end_s": 2.5, "confidence": 0.7, "tags": ["mount"], "cut_at_start": False,
         "cut_at_end": False, "evidence": "phone in mount"}], "unusable": [], "extra": 1}))
    a = client.get(f"/api/manual/{s['name']}").json()["answer"]
    assert a["ok"] is False   # an unknown field
    (root / "prelabels.json").write_text(json.dumps({"video_id": video_id, "model": "agent-x", "candidates": [
        {"start_s": 1.0, "end_s": 2.5, "confidence": 0.7, "tags": ["mount"], "cut_at_start": False,
         "cut_at_end": False, "evidence": "phone in mount"}], "unusable": []}))
    a = client.get(f"/api/manual/{s['name']}").json()["answer"]
    assert a == {"ok": True, "model": "agent-x", "candidates": 1, "unusable": 0}

    r = client.post(f"/api/manual/{s['name']}/import", headers=h, json={"model": ""})   # model from the file
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == "agent-x_prelabel-dir-v1" and r.json()["candidates"] == 1
    row = ws.video(video_id)
    assert row["step"] == "prelabeled" and row["prelabel_run"] == "agent-x_prelabel-dir-v1"
    pre = client.get(f"/api/videos/{video_id}").json()["prelabel"]
    assert pre["covered"] == [[0.0, 7.5]] and pre["candidates"][0]["tags"] == ["mount"]
    assert client.get("/api/manual/../etc").status_code == 404

def _frame_lumas(path: Path) -> list[float]:
    out = subprocess.run([ffmpeg.binaries()[0], "-hide_banner", "-nostdin", "-i", str(path), "-vf",
                          "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stdout
    return [float(line.split("=")[1]) for line in out.splitlines() if "YAVG" in line]


def test_export_clip_frames_are_exact(tmp_path):
    """Clip frame i is normalized frame a + i, at time i / fps (docs/design.md section 8.1)."""
    lib = tmp_path / "lib"
    lib.mkdir()
    ff, fp = ffmpeg.binaries()
    # The gray level codes the frame number; 30 fps in, so the normalize step drops frames.
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "nullsrc=s=160x120:r=30:d=6,format=gray,geq=lum='20+mod(N\\,200)'",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(lib / "gray.mp4")], check=True)
    ws = Workspace(tmp_path / "ws")
    ws.settings.libraries = [str(lib)]
    ws.settings.labeler = "tester"
    jobs.scan(ws)
    (video_id,) = [r["video_id"] for r in ws.videos()]
    jobs.process_video(ws, video_id, None)
    label = store.new_label(video_id, "tester", 15, 90, None)
    label["watched"] = [[37, 80]]   # starts between keyframes
    store.save(ws.label_path(video_id), label, ws.label_names())

    root = Path(export.export(ws)["path"])
    clip = root / "videos" / f"{video_id}_000037.mp4"
    want = _frame_lumas(ws.video_path(video_id))[37:81]
    got = _frame_lumas(clip)
    assert len(got) == len(want) == 44
    assert all(abs(g - w) < 0.6 for g, w in zip(got, want))
    pts = subprocess.run([fp, "-v", "error", "-select_streams", "v", "-show_entries", "frame=pts_time",
                          "-of", "csv=p=0", str(clip)], capture_output=True, text=True, check=True).stdout.split()
    assert [round(float(t.rstrip(",")) * 15, 3) for t in pts] == list(range(44))
