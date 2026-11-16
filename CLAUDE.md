# CLAUDE.md

Local macOS app (`video-label`) for frame-accurate labeling of video events. Candidate events
come from a chat model used by hand: the "Prepare prelabel" page writes the frames of a video and a
README.md into one directory, and imports the `prelabels.json` that the model writes. A person confirms/fixes them in a browser UI
served by a local FastAPI server; only watched time is exported as clips + JSON labels.
The Grok (xAI API) path is kept in the code but runs only from the CLI (`video-label process`
with a key); the app UI and the background worker never call Grok.
Labels are configured on the Setup page (`Settings.labels`); the seed is `phone`=1,
`driving`=0, `ignore`=−1. The shipped prompt targets phone use.

## Documentation — read before changing behavior

- [README.md](README.md) — install, run, CLI commands, privacy, license.
- [docs/design.md](docs/design.md) — the spec: pipeline steps (§5), workspace layout (§5),
  labeling screen and keys (§6), watched-time rules (§6.2), training-window panel (§7),
  export steps / layout / label-file format (§8), code map (§10).
- [docs/prelabel-prompt.md](docs/prelabel-prompt.md) — chunking, the system prompt (§3),
  the answer schema (§4), the pre-label file format (§5), how to evaluate pre-labels (§6).

When code changes a format, key, CLI command, or default described there, update the doc in the
same change.

## Commands

```sh
curl -fsSL https://raw.githubusercontent.com/giladmanor/video-labeling-app/main/install.sh | sh  # user install (install.sh)
VIDEO_LABEL_SOURCE=$PWD sh install.sh                        # test the installer on this checkout
sh uninstall.sh                                              # remove what install.sh added (reads its receipt)
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # dev setup
.venv/bin/video-label                                        # run (http://127.0.0.1:8765/)
.venv/bin/video-label --workspace /tmp/ws serve --no-browser # scratch workspace; --workspace goes before the subcommand
.venv/bin/python -m pytest -q                                # tests (need ffmpeg; generate a test video)
```

Env vars: `VIDEO_LABEL_WORKSPACE`, `VIDEO_LABEL_FFMPEG` / `VIDEO_LABEL_FFPROBE`, `XAI_API_KEY`
(CLI Grok path only; overrides the Keychain).

## Code map (`video_label/`)

| file | role |
|---|---|
| [cli.py](video_label/cli.py) | argparse entry point `video-label`; subcommands serve, add-library, process, import-prelabel, set-key, models |
| [server.py](video_label/server.py) | `create_app(ws)`: FastAPI routes `/api/*`, `/media/{id}.mp4` (range requests), static files; `X-Video-Label` header guard on non-GET `/api/` |
| [workspace.py](video_label/workspace.py) | `Workspace` (dirs, `config.json` → `Settings` dataclass, `state.sqlite` with `videos`/`files` tables, `label_names()`), `STEPS`, `SEED_LABELS`, `write_json_atomic` |
| [jobs.py](video_label/jobs.py) | `scan` (sha256 → `video_id` = first 12 hex), `process_video` (probe → normalize; frames → prelabel only with a Grok client), background `Worker` thread (no Grok) |
| [ffmpeg.py](video_label/ffmpeg.py) | binary discovery, `probe`, `normalize` (H.264, 15 fps CFR, ≤720p, 1 s GOP), `extract_frames` |
| [grok.py](video_label/grok.py) | `GrokClient` (OpenAI-compatible chat, retries on 408/429/5xx), Keychain key storage — CLI only |
| [prelabel.py](video_label/prelabel.py) | `plan_chunks`, `build_messages`, `parse_answer`, `check_answer` (schema check), `merge_candidates`, `prelabel_video`, `assemble` (chunk results → pre-label file), `manual_prelabel`; `TAGS` |
| [manual.py](video_label/manual.py) | "Prepare prelabel" page: `prepare` (all frames in one directory + README.md from [prompts/prelabel-dir-v1.md](video_label/prompts/prelabel-dir-v1.md)), `paste_instruction`, `read_answer` / `import_set` (the model's `prelabels.json` → pre-label run) |
| [store.py](video_label/store.py) | label file per video: `new_label`, `validate` (against the configured label values), `save`, `filled_segments` (watched gaps → 0), `frame_labels`; `check_label_config` |
| [windows.py](video_label/windows.py) | `WindowConfig`/`CONFIGS` (`causal-2s`), `window_labels`, `summarize` for the training-examples panel |
| [export.py](video_label/export.py) | `stats(ws)` (Videos page "Training data") and `export(ws)`: the watched ranges of all videos that are not skipped → one re-encoded clip each, label JSON, `videos.csv` / `segments.csv`, `export.json` |
| [prompts/prelabel-v1.txt](video_label/prompts/prelabel-v1.txt) | system prompt; must match docs/prelabel-prompt.md §3 verbatim |
| [static/app.js](video_label/static/app.js) | single-page UI: hash routing (`#setup`, `#jobs`, and for the selected video `CUR` `#prelabel/<id>`, `#label/<id>`), `markNextSetting` (Setup glow), label config (`applyLabels`), labeling state `L`, `KEYMAP`, watched tracking via `requestVideoFrameCallback`, canvas timelines, autosave |
| [static/index.html](video_label/static/index.html), [static/style.css](video_label/static/style.css) | markup and theme tokens |

Tests: [tests/test_logic.py](tests/test_logic.py) (pure functions) and
[tests/test_pipeline.py](tests/test_pipeline.py) (end-to-end with a generated ffmpeg video and a
`FakeGrok`; exercises the API via `TestClient(create_app(ws, start_worker=False))`).

## Invariants — don't break these

- **Frame numbers refer to the normalized copy** (`videos/{video_id}.mp4`), never the source.
  Segments and watched ranges are inclusive `[start_frame, end_frame]`.
- **Libraries are read-only.** Never write, move, or delete files under a library path.
- **Atomic writes.** Persist JSON with `write_json_atomic`; ffmpeg outputs go to `*.part*` then rename.
- **Resumable jobs.** A video's `step` is the last finished step (`workspace.STEPS`); `status` is
  `pending | running | ok | error` (`waiting_key` only in old workspaces). The worker resets
  `running`/`waiting_key` to `pending` on each wake and runs `process_video(..., client=None)`,
  which stops after `normalized`.
- **Only watched, human-confirmed time is exported.** Unwatched frames are `None` in
  `frame_labels` and never appear in an export. Exported segments must tile each clip exactly
  (`export.clip_segments` raises otherwise).
- **Clips are re-encoded, not stream-copied**, so cuts are frame-exact.
- **Prompt versions are immutable.** Change the prompt by adding `prompts/prelabel-vN.txt` and a
  new `prompt_version`; keep `prelabel.TAGS` in sync with the prompt and the schema in the doc.
- **Label roles are fixed by value:** values ≥ 1 are events (positive windows), 0 is the
  background (watched-gap fill, reject), −1 is ignore. 0 and −1 always exist. Values are
  stable ids written to label files — never renumber or reuse them. Each label also has a
  user-chosen `key` (one character, not in `store.RESERVED_KEYS`) and `color` (`#rrggbb`;
  new labels default to the first unused `workspace.LABEL_PALETTE` color; old configs are
  filled by `fill_label_defaults`). Never hard-code a label
  name or key; use `ws.label_names()` / `labels()` in app.js.
- **The API key never goes in a file** — Keychain or `XAI_API_KEY` only.
- The server binds to `127.0.0.1` and only accepts the `Host` names `127.0.0.1`/`localhost`
  (DNS-rebinding guard; tests use `base_url="http://127.0.0.1"`). Mutating requests from the page must send `X-Video-Label: 1`
  (`api()` in app.js does this).

## Conventions

- Python ≥ 3.11, `from __future__ import annotations`, type hints, module docstrings that point
  at the relevant doc section. Plain dicts for label/prelabel JSON, dataclasses for config.
- No frontend build step or framework: vanilla JS in one file, edited directly.
- Docs use short, plain sentences; keep that style when editing them.
- Keep tests fast and offline: use `FakeGrok`, never the real API.
