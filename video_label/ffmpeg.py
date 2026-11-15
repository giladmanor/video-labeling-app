"""ffmpeg and ffprobe: find the binaries, probe, normalize, and extract frames."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from fractions import Fraction
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def binaries() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe).

    Order: the VIDEO_LABEL_FFMPEG / VIDEO_LABEL_FFPROBE variables, then the binaries on PATH,
    then the pinned static build of the static-ffmpeg package (downloaded on first use).
    """
    env_ff, env_fp = os.environ.get("VIDEO_LABEL_FFMPEG"), os.environ.get("VIDEO_LABEL_FFPROBE")
    if env_ff and env_fp:
        return env_ff, env_fp
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff and fp:
        return ff, fp
    try:
        import static_ffmpeg.run
    except ImportError as e:
        raise FFmpegError("ffmpeg is not installed and static-ffmpeg is missing") from e
    ff, fp = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
    return ff, fp


@lru_cache(maxsize=1)
def version() -> str:
    out = _run([binaries()[0], "-hide_banner", "-version"])
    return out.splitlines()[0].split(" Copyright")[0].removeprefix("ffmpeg version ").strip()


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
        raise FFmpegError(f"{Path(cmd[0]).name} failed: {tail}")
    return proc.stdout


@dataclass
class Probe:
    duration_s: float
    fps: float
    width: int
    height: int
    codec: str
    n_frames: int | None


def probe(path: Path, count_frames: bool = False) -> Probe:
    cmd = [binaries()[1], "-v", "error", "-select_streams", "v:0"]
    if count_frames:
        cmd.append("-count_frames")
    cmd += [
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames,nb_read_frames",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    data = json.loads(_run(cmd))
    if not data.get("streams"):
        raise FFmpegError("no video stream")
    s = data["streams"][0]
    rate = s.get("avg_frame_rate") or "0/1"
    if rate in ("0/0", "0/1"):
        rate = s.get("r_frame_rate") or "0/1"
    fps = float(Fraction(rate)) if rate != "0/0" else 0.0
    n = s.get("nb_read_frames") if count_frames else s.get("nb_frames")
    return Probe(
        duration_s=float(data.get("format", {}).get("duration") or 0.0),
        fps=fps,
        width=int(s["width"]),
        height=int(s["height"]),
        codec=s.get("codec_name", ""),
        n_frames=int(n) if n and str(n).isdigit() else None,
    )


def normalize(src: Path, dst: Path, fps: int, max_height: int, crf: int = 18) -> None:
    """Write the canonical playback copy: H.264, constant fps, one keyframe each second, no audio."""
    tmp = dst.with_name(dst.stem + ".part.mp4")
    vf = f"fps={fps},scale=-2:'min({max_height},ih)':flags=lanczos,setsar=1"
    _run([
        binaries()[0], "-hide_banner", "-loglevel", "error", "-y", "-nostdin", "-i", str(src),
        "-map", "0:v:0", "-vf", vf,
        "-c:v", "libx264", "-crf", str(crf), "-preset", "fast", "-pix_fmt", "yuv420p",
        "-g", str(fps), "-keyint_min", str(fps), "-sc_threshold", "0",
        "-an", "-movflags", "+faststart", str(tmp),
    ])
    tmp.replace(dst)


def extract_frames(src: Path, out_dir: Path, sample_fps: float, height: int) -> list[Path]:
    """Extract frames at sample_fps. Frame k (0-based) is at time k / sample_fps."""
    tmp = out_dir.with_name(out_dir.name + ".part")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    vf = f"fps={sample_fps}:round=down,scale=-2:'min({height},ih)'"
    _run([
        binaries()[0], "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
        "-vf", vf, "-q:v", "3", str(tmp / "%06d.jpg"),
    ])
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp.replace(out_dir)
    return sorted(out_dir.glob("*.jpg"))
