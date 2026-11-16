"""Pre-label a video with Grok: chunk the 2 fps frames, ask for candidates, merge, save.

The instruction, the answer schema, and the file format are in docs/prelabel-prompt.md.
"""

from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from .grok import GrokClient, GrokError

TAGS = [
    "mount", "hands_free", "phone_not_looked_at", "food_drink",
    "hand_on_face", "phone_hidden", "night_ir", "bad_video",
]
MERGE_GAP_S = 1.0


def load_prompt(version: str) -> str:
    return resources.files("video_label").joinpath("prompts", f"{version}.txt").read_text()


def run_id_for(model: str, prompt_version: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", f"{model}_{prompt_version}")


def plan_chunks(n_frames: int, sample_fps: float, chunk_s: float, overlap_s: float) -> list[tuple[int, int]]:
    """Frame index ranges [i0, i1) of the chunks. Chunks overlap by overlap_s."""
    size = max(1, round(chunk_s * sample_fps))
    step = max(1, size - round(overlap_s * sample_fps))
    chunks, i0 = [], 0
    while i0 < n_frames:
        i1 = min(n_frames, i0 + size)
        chunks.append((i0, i1))
        if i1 == n_frames:
            break
        i0 += step
    return chunks


def frame_time(index: int, sample_fps: float) -> float:
    return round(index / sample_fps, 1)


def build_messages(prompt: str, frames: list[tuple[float, Path]]) -> list[dict]:
    content: list[dict] = []
    for t, path in frames:
        content.append({"type": "text", "text": f"t={t:.1f}"})
        b64 = base64.b64encode(path.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})
    return [{"role": "system", "content": prompt}, {"role": "user", "content": content}]


def parse_answer(text: str) -> dict:
    """Read the JSON object in the answer, also when the model adds fences or text around it."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


def _number(v, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{name} is not a number")
    return float(v)


def check_answer(obj: dict, t0: float, t1: float, tolerance_s: float = 0.6) -> dict:
    """Check a chunk answer against the schema (docs/prelabel-prompt.md section 4).

    Times must be inside the chunk [t0, t1]. Returns a cleaned copy; raises ValueError.
    """
    if not isinstance(obj, dict) or set(obj) != {"candidates", "unusable"}:
        raise ValueError("the answer must have exactly the keys 'candidates' and 'unusable'")

    def times(item: dict) -> tuple[float, float]:
        s, e = _number(item.get("start_s"), "start_s"), _number(item.get("end_s"), "end_s")
        if s > e:
            raise ValueError(f"start_s {s} > end_s {e}")
        if s < t0 - tolerance_s or e > t1 + tolerance_s:
            raise ValueError(f"range {s}-{e} is outside the chunk {t0}-{t1}")
        return max(s, t0), min(e, t1)

    candidates = []
    for c in obj["candidates"]:
        s, e = times(c)
        conf = _number(c.get("confidence"), "confidence")
        if not 0 <= conf <= 1:
            raise ValueError("confidence is not in 0..1")
        tags = c.get("tags", [])
        if not isinstance(tags, list) or any(t not in TAGS for t in tags):
            raise ValueError(f"unknown tags: {tags}")
        candidates.append({
            "start_s": s, "end_s": e, "confidence": conf, "tags": sorted(set(tags)),
            "evidence": str(c.get("evidence", ""))[:500],
        })
    unusable = []
    for u in obj["unusable"]:
        s, e = times(u)
        unusable.append({"start_s": s, "end_s": e, "reason": str(u.get("reason", ""))[:200]})
    return {"candidates": candidates, "unusable": unusable}


def merge_candidates(items: list[dict], gap_s: float = MERGE_GAP_S) -> list[dict]:
    """Join candidates that overlap or have a gap of gap_s or less (chunk overlaps give duplicates)."""
    merged: list[dict] = []
    for c in sorted(items, key=lambda c: c["start_s"]):
        last = merged[-1] if merged else None
        if last and c["start_s"] <= last["end_s"] + gap_s:
            last["end_s"] = max(last["end_s"], c["end_s"])
            last["tags"] = sorted(set(last["tags"]) | set(c["tags"]))
            if c["confidence"] > last["confidence"]:
                last["confidence"], last["evidence"] = c["confidence"], c["evidence"]
        else:
            merged.append(dict(c))
    for i, c in enumerate(merged, 1):
        c["id"] = f"c{i:03d}"
    return merged


def merge_ranges(items: list[dict], gap_s: float = MERGE_GAP_S) -> list[dict]:
    merged: list[dict] = []
    for u in sorted(items, key=lambda u: u["start_s"]):
        if merged and u["start_s"] <= merged[-1]["end_s"] + gap_s:
            merged[-1]["end_s"] = max(merged[-1]["end_s"], u["end_s"])
        else:
            merged.append(dict(u))
    return merged


def prelabel_video(
    client: GrokClient,
    *,
    video_id: str,
    frames: list[Path],
    duration_s: float,
    sample_fps: float,
    chunk_s: float,
    overlap_s: float,
    prompt_version: str,
    parallel: int = 2,
) -> dict:
    """Run all chunks of one video and return the pre-label file content."""
    prompt = load_prompt(prompt_version)
    chunks = plan_chunks(len(frames), sample_fps, chunk_s, overlap_s)

    def run_chunk(chunk: tuple[int, int]) -> dict:
        i0, i1 = chunk
        timed = [(frame_time(i, sample_fps), frames[i]) for i in range(i0, i1)]
        t0, t1 = timed[0][0], timed[-1][0]
        messages = build_messages(prompt, timed)
        usage_total: dict[str, int] = {}
        error = ""
        for _ in range(2):  # one retry when the answer fails the schema check
            try:
                text, usage = client.chat(messages)
            except GrokError as e:
                return {"t0": t0, "t1": t1, "error": str(e), "usage": usage_total}
            for k, v in usage.items():
                if isinstance(v, int):
                    usage_total[k] = usage_total.get(k, 0) + v
            try:
                answer = check_answer(parse_answer(text), t0, t1)
                return {"t0": t0, "t1": t1, "answer": answer, "usage": usage_total}
            except (ValueError, json.JSONDecodeError) as e:
                error = f"bad answer: {e}"
        return {"t0": t0, "t1": t1, "error": error, "usage": usage_total}

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        results = list(pool.map(run_chunk, chunks))
    return assemble(results, video_id=video_id, model=client.model, prompt_version=prompt_version,
                    sample_fps=sample_fps, duration_s=duration_s)


def assemble(results: list[dict], *, video_id: str, model: str, prompt_version: str,
             sample_fps: float, duration_s: float) -> dict:
    """The pre-label file content from chunk results: {t0, t1, usage, and answer or error}."""
    ok = [r for r in results if "answer" in r]
    usage: dict[str, int] = {}
    for r in results:
        for k, v in r["usage"].items():
            usage[k] = usage.get(k, 0) + v
    covered = merge_ranges([{"start_s": r["t0"], "end_s": r["t1"]} for r in ok], gap_s=1 / sample_fps + 1e-6)
    return {
        "video_id": video_id,
        "run_id": run_id_for(model, prompt_version),
        "model": model,
        "prompt_version": prompt_version,
        "sample_fps": sample_fps,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": duration_s,
        "covered": [[c["start_s"], c["end_s"]] for c in covered],
        "failed_chunks": [{"start_s": r["t0"], "end_s": r["t1"], "error": r["error"]} for r in results if "error" in r],
        "candidates": merge_candidates([c for r in ok for c in r["answer"]["candidates"]]),
        "unusable": merge_ranges([u for r in ok for u in r["answer"]["unusable"]]),
        "usage": usage,
    }


def manual_prelabel(data: dict, *, video_id: str, duration_s: float, model: str, prompt_version: str) -> dict:
    """Build a pre-label file from candidates made outside the Grok API (for example by hand
    or by another model, when there is no Grok key). The candidates must pass the same check.
    """
    answer = check_answer(
        {"candidates": data.get("candidates", []), "unusable": data.get("unusable", [])}, 0.0, duration_s
    )
    return {
        "video_id": video_id,
        "run_id": run_id_for(model, prompt_version),
        "model": model,
        "prompt_version": prompt_version,
        "sample_fps": data.get("sample_fps", 2.0),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": duration_s,
        "covered": [[0.0, duration_s]],
        "failed_chunks": [],
        "candidates": merge_candidates(answer["candidates"]),
        "unusable": merge_ranges(answer["unusable"]),
        "usage": {},
    }
