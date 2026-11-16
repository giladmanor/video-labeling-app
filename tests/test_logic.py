import pytest

from video_label import prelabel, store, windows


def test_plan_chunks_overlap_and_end():
    # 67 s at 2 fps = 134 frames; 30 s chunks (60 frames) with 4 s overlap (8 frames) -> step 52
    chunks = prelabel.plan_chunks(134, 2.0, 30.0, 4.0)
    assert chunks == [(0, 60), (52, 112), (104, 134)]
    assert prelabel.plan_chunks(10, 2.0, 30.0, 4.0) == [(0, 10)]
    assert prelabel.plan_chunks(0, 2.0, 30.0, 4.0) == []


def test_parse_answer_with_fences():
    assert prelabel.parse_answer('```json\n{"candidates": [], "unusable": []}\n```') == {"candidates": [], "unusable": []}
    with pytest.raises(ValueError):
        prelabel.parse_answer("no json here")


def test_check_answer_valid_and_clamped():
    obj = {
        "candidates": [{"start_s": 9.8, "end_s": 12.0, "confidence": 0.7, "tags": ["mount"],
                        "cut_at_start": True, "cut_at_end": False, "evidence": "phone in mount"}],
        "unusable": [],
    }
    out = prelabel.check_answer(obj, 10.0, 39.5)
    assert out["candidates"][0]["start_s"] == 10.0  # clamped into the chunk


@pytest.mark.parametrize("bad", [
    {"candidates": []},
    {"candidates": [{"start_s": 5, "end_s": 4, "confidence": 0.5, "tags": []}], "unusable": []},
    {"candidates": [{"start_s": 1, "end_s": 2, "confidence": 1.5, "tags": []}], "unusable": []},
    {"candidates": [{"start_s": 1, "end_s": 2, "confidence": 0.5, "tags": ["phone"]}], "unusable": []},
    {"candidates": [{"start_s": 50, "end_s": 52, "confidence": 0.5, "tags": []}], "unusable": []},
])
def test_check_answer_rejects(bad):
    with pytest.raises(ValueError):
        prelabel.check_answer(bad, 0.0, 29.5)


def test_merge_candidates_across_chunks():
    items = [
        {"start_s": 25.0, "end_s": 29.5, "confidence": 0.6, "tags": ["phone_hidden"], "evidence": "a"},
        {"start_s": 26.0, "end_s": 31.0, "confidence": 0.9, "tags": [], "evidence": "b"},
        {"start_s": 40.0, "end_s": 41.0, "confidence": 0.3, "tags": [], "evidence": "c"},
    ]
    out = prelabel.merge_candidates(items)
    assert [(c["id"], c["start_s"], c["end_s"], c["confidence"], c["evidence"]) for c in out] == [
        ("c001", 25.0, 31.0, 0.9, "b"), ("c002", 40.0, 41.0, 0.3, "c"),
    ]
    assert out[0]["tags"] == ["phone_hidden"]


def _label(segments, watched, n=100):
    lab = store.new_label("v1", "t", 10.0, n, None)
    lab["segments"], lab["watched"] = segments, watched
    return store.validate(lab)


def seg(a, b, label=1, origin="manual"):
    return {"start_frame": a, "end_frame": b, "label": label, "origin": origin, "tags": []}


def test_validate_rejects_overlap_and_range():
    with pytest.raises(ValueError):
        _label([seg(0, 10), seg(10, 20)], [])
    with pytest.raises(ValueError):
        _label([seg(90, 100)], [])


def test_watched_ranges_merge():
    assert _label([], [[10, 20], [21, 30], [50, 40], [5, 12]])["watched"] == [[5, 30]]


def test_fill_only_watched_time():
    lab = _label([seg(20, 29)], [[10, 39], [60, 69]])
    filled = [(s["start_frame"], s["end_frame"], s["label"], s["origin"]) for s in store.filled_segments(lab)]
    assert filled == [(10, 19, 0, "watched"), (20, 29, 1, "manual"), (30, 39, 0, "watched"), (60, 69, 0, "watched")]
    labels = store.frame_labels(lab)
    assert labels[0] is None and labels[45] is None and labels[25] == 1 and labels[65] == 0


def test_window_end_rule():
    # fps 10: window 20 frames, stride 5, end part 5 frames
    cfg = windows.CONFIGS["causal-2s"]
    lab = _label([seg(30, 49)], [[0, 99]])
    wl = windows.window_labels(store.frame_labels(lab), 10.0, cfg)
    # windows end at 20, 25, ..., 100 (17 windows)
    assert len(wl) == 17
    ends = list(range(20, 101, 5))
    by_end = dict(zip(ends, wl))
    assert by_end[30] == 0      # end part 25-29: before the event
    assert by_end[35] == 1      # end part 30-34: the first window that sees the event "now"
    assert by_end[50] == 1      # end part 45-49
    assert by_end[55] == 0      # end part 50-54: the phone is put down


def test_window_needs_one_watched_range():
    cfg = windows.CONFIGS["causal-2s"]
    lab = _label([], [[0, 14], [20, 34]])   # two parts of 1.5 s: too short for a 2 s window
    assert windows.window_labels(store.frame_labels(lab), 10.0, cfg) == []
    s = windows.summarize([lab], cfg)
    assert s["short_watched_parts"] == 2 and s["negative_windows"] == 0


def test_events_count_only_when_watched():
    cfg = windows.CONFIGS["causal-2s"]
    lab = _label([seg(10, 19), seg(60, 69), seg(80, 89, label=0, origin="grok_rejected")], [[0, 39]])
    s = windows.summarize([lab], cfg)
    assert s["events"] == 1 and s["hard_negatives"] == 0


def test_manual_prelabel_checks_and_merges():
    data = {"candidates": [
        {"start_s": 4.0, "end_s": 9.5, "confidence": 0.8, "tags": [], "evidence": "phone in hands"},
        {"start_s": 9.8, "end_s": 12.0, "confidence": 0.6, "tags": ["phone_hidden"], "evidence": "x"},
    ], "unusable": []}
    out = prelabel.manual_prelabel(data, video_id="v1", duration_s=67.4, model="claude-opus-5-5", prompt_version="prelabel-v1")
    assert out["run_id"] == "claude-opus-5-5_prelabel-v1"
    assert [(c["id"], c["start_s"], c["end_s"]) for c in out["candidates"]] == [("c001", 4.0, 12.0)]
    with pytest.raises(ValueError):
        prelabel.manual_prelabel({"candidates": [{"start_s": 70, "end_s": 71, "confidence": 0.5, "tags": []}]},
                                 video_id="v1", duration_s=67.4, model="m", prompt_version="prelabel-v1")


def test_clip_segments_cover_the_clip():
    from video_label.export import clip_segments
    lab = _label([seg(20, 29), seg(35, 60, label=-1, origin="unsure")], [[10, 49]])
    out = [(s["start_frame"], s["end_frame"], s["label"]) for s in clip_segments(lab, 10, 49)]
    assert out == [(0, 9, 0), (10, 19, 1), (20, 24, 0), (25, 39, -1)]


def test_label_config():
    from video_label.workspace import SEED_LABELS
    seed = [dict(x) for x in SEED_LABELS]
    out = store.check_label_config([seed[2], {"value": 12, "name": "eating", "key": "E", "color": "#7048E8"}] + seed[:2])
    assert [(x["value"], x["key"], x["color"]) for x in out] == [
        (1, "1", "#d9480f"), (12, "e", "#7048e8"), (0, "0", "#2f9e44"), (-1, "u", "#868e96")]
    ev = lambda **kw: {"value": 2, "name": "x", "key": "x", "color": "#123456", **kw}
    for bad in (seed[:2], seed[1:], seed + [ev(value=100)], seed + [ev(name="phone")],
                seed + [ev(name="bad name!")], seed + [ev(value=1)], seed + [ev(key="1")],
                seed + [ev(key="a")], seed + [ev(key="u")], seed + [ev(key="xy")], seed + [ev(key="")],
                seed + [ev(color="red")], seed + [ev(color="")]):
        with pytest.raises(ValueError):
            store.check_label_config(bad)


def test_more_event_labels():
    lab = store.new_label("v1", "t", 10.0, 100, None)
    lab["segments"], lab["watched"] = [seg(10, 19), seg(30, 39, label=2)], [[0, 99]]
    with pytest.raises(ValueError):
        store.validate(lab)   # 2 is not a seed label
    lab = store.validate(lab, {1, 2, 0, -1})
    s = windows.summarize([lab], windows.CONFIGS["causal-2s"], {1: "phone", 2: "eating"})
    assert s["events"] == 2 and s["events_by_label"] == {"phone": 1, "eating": 1}
    assert s["positive_windows"] > 0


def test_old_config_gets_label_keys_and_colors(tmp_path):
    import json
    from video_label.workspace import Workspace
    (tmp_path / "config.json").write_text(json.dumps({"labels": [
        {"value": 1, "name": "phone"}, {"value": 2, "name": "eating"},
        {"value": 0, "name": "driving"}, {"value": -1, "name": "ignore"}]}))
    labels = Workspace(tmp_path).settings.labels
    assert [x["key"] for x in labels] == ["1", "2", "0", "u"]
    assert [x["color"] for x in labels] == ["#d9480f", "#7048e8", "#2f9e44", "#868e96"]


def test_page_has_every_element_that_app_js_uses():
    # A missing element makes app.js stop at load, and then no button on any page works.
    import re
    from importlib import resources
    static = resources.files("video_label").joinpath("static")
    js, html = static.joinpath("app.js").read_text(), static.joinpath("index.html").read_text()
    ids = set(re.findall(r'\$\("#([\w-]+)', js))
    assert ids and not {i for i in ids if f'id="{i}"' not in html}
