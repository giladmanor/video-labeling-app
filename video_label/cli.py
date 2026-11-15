"""Command line: `video-label` starts the app; other commands help with setup and tests."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import threading
import webbrowser
from pathlib import Path

from . import grok, jobs, prelabel
from .workspace import Workspace, write_json_atomic


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="video-label")
    ap.add_argument("--workspace", help="app data directory (default ~/video-labeling)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="start the app (default)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")

    p = sub.add_parser("add-library", help="add a video library directory")
    p.add_argument("path")

    sub.add_parser("process", help="scan and process all videos now, without the server")

    p = sub.add_parser("import-prelabel", help="use candidates from a JSON file instead of Grok")
    p.add_argument("file", help="JSON with video_id, model, and candidates (docs/prelabel-prompt.md)")

    sub.add_parser("set-key", help="store the xAI API key in the macOS Keychain")
    sub.add_parser("models", help="list the Grok models that the key can use")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ws = Workspace(args.workspace)
    cmd = args.cmd or "serve"

    if cmd == "serve":
        import uvicorn

        from .server import create_app

        port = getattr(args, "port", 8765)
        url = f"http://127.0.0.1:{port}/"
        if not getattr(args, "no_browser", False):
            threading.Timer(1.0, webbrowser.open, (url,)).start()
        print(f"video-label: {url}  (workspace {ws.root})")
        uvicorn.run(create_app(ws), host="127.0.0.1", port=port, log_level="warning")

    elif cmd == "add-library":
        path = str(Path(args.path).expanduser().resolve())
        if not Path(path).is_dir():
            ap.error(f"not a directory: {path}")
        if path not in ws.settings.libraries:
            ws.settings.libraries.append(path)
            ws.save_settings()
        print("libraries:", *ws.settings.libraries, sep="\n  ")

    elif cmd == "process":
        new = jobs.scan(ws)
        print(f"{new} new videos")
        key = grok.get_key()
        client = grok.GrokClient(key, ws.settings.grok_base_url, ws.settings.grok_model) if key else None
        if client is None:
            print("no API key: stopping before the Grok step")
        ws.execute("UPDATE videos SET status = 'pending' WHERE status != 'ok' OR step != 'prelabeled'")
        for r in ws.execute("SELECT video_id, rel_path FROM videos WHERE status = 'pending'"):
            try:
                jobs.process_video(ws, r["video_id"], client)
                row = ws.video(r["video_id"])
                print(f"{r['video_id']}  {row['step']:<10} {row['status']:<11} {r['rel_path']}")
            except Exception as e:
                ws.update_video(r["video_id"], status="error", error=str(e)[:500])
                print(f"{r['video_id']}  ERROR {e}")

    elif cmd == "import-prelabel":
        data = json.loads(Path(args.file).read_text())
        row = ws.video(data.get("video_id", ""))
        if row is None or row["duration_s"] is None or row["step"] in ("scanned", "hashed", "probed"):
            ap.error(f"video {data.get('video_id')!r} is not in the workspace or not processed yet")
        model = data.get("model") or ap.error("the file needs a 'model' name, for example 'manual'")
        result = prelabel.manual_prelabel(
            data, video_id=row["video_id"], duration_s=row["duration_s"],
            model=model, prompt_version=data.get("prompt_version", ws.settings.prompt_version),
        )
        write_json_atomic(ws.prelabel_path(result["run_id"], row["video_id"]), result)
        ws.update_video(row["video_id"], step="prelabeled", status="ok", error=None, prelabel_run=result["run_id"])
        print(f"{row['video_id']}: {len(result['candidates'])} candidates imported as run {result['run_id']}")

    elif cmd == "set-key":
        grok.set_key(getpass.getpass("xAI API key: "))
        print("stored in the macOS Keychain")

    elif cmd == "models":
        key = grok.get_key()
        if not key:
            ap.error("no API key; run `video-label set-key` or set XAI_API_KEY")
        client = grok.GrokClient(key, ws.settings.grok_base_url, ws.settings.grok_model)
        for m in client.models():
            print(m["id"], "(image input)" if m["image_input"] else "")


if __name__ == "__main__":
    main()
