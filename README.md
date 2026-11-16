# video-labeling-app

A local macOS app for labeling events in videos quickly and with frame accuracy.

A vision-language model looks at frames of each video and proposes candidate events: the app
prepares a directory of frames and instructions, a model session that can read it (for example
Claude) writes the candidates to a JSON file, and the app imports it. A person then reviews each candidate in a browser-based labeling screen:
accepts or rejects it, fixes the start and end frames, and adds events the model missed.
The app tracks which parts of each video the person actually watched, and exports only those
parts, as new clips with one JSON label file each.

The included prompt and label names target one event type: a driver handling a phone
(`phone` = 1, `driving` = 0, `ignore` = −1).

## Features

- Add one or more video libraries (folders); the app never changes the original files.
- Automatic processing with ffmpeg: playback copies at a constant frame rate.
- Prepare prelabel: files for a labeling LLM used by hand: all the frames of a video in one directory
  with a README.md; paste a short instruction into a model session that can read the
  directory, and the app checks and imports the prelabels.json that the model writes.
  Candidates can also come from a JSON file.
- Labels set on the Setup page (seed: phone, driving, ignore), with more event labels if needed.
- A one-window labeling screen: candidate navigation, play/pause and speed, accept / reject /
  unsure, manual segments, draggable timelines, tags, undo, and keyboard shortcuts.
- Watched-time tracking: only time a person watched is exported.
- Live counts of the training examples that the labels give.
- Export of frame-exact clips, label files, and CSV manifests.

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.11 or newer
- ffmpeg (Homebrew `brew install ffmpeg`; if it is missing, the app downloads a static build)

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/giladmanor/video-labeling-app
```

Or from a clone:

```sh
git clone https://github.com/giladmanor/video-labeling-app
cd video-labeling-app
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Run

```sh
video-label            # or .venv/bin/video-label from a clone
```

The app starts on http://127.0.0.1:8765/ and opens the browser. The first time, it opens the Setup
page, and the next setting to fill in glows. When the settings are complete, it opens the video list.

1. **Setup:** enter your name, add a video library folder, and check the labels.
2. **Videos:** click "Scan and process". Each video gets a playback copy.
3. **Pre-label:** click "Export for prelabeling" on a video and "Prepare directory". Copy the
   instruction into a model session that can read the directory (for example Claude Code).
   When the model has written `prelabels.json`, click "Import".
4. **Label:** open a video. Press `N` for the next suggestion, then `A` (accept), `R` (reject),
   or `U` (unsure), and fix the edges with `I` / `O`. Press `?` for all keys.
   Press `Enter` when a video is done.
5. **Export:** the Videos page shows the training data so far: the seconds of watched time and
   the instances of each label. Click "Export". All the parts you watched are exported, also of
   videos that are not done; skipped videos are not. The export goes to `~/video-labeling/exports/`.

Options: `--workspace DIR` (default `~/video-labeling`), `serve --port N`, `serve --no-browser`.

### Candidates from a file

You can also label a video with no suggestions, or import candidates from a JSON file made by
another model or a person:

```sh
video-label import-prelabel candidates.json
```

The file format is in [docs/prelabel-prompt.md](docs/prelabel-prompt.md).

### Other commands

```sh
video-label add-library PATH   # add a library folder
video-label process            # process all videos without the server
```

The Grok (xAI API) pre-label step is not in the app any more. It is still in the code and runs
only from the command line: `video-label set-key` stores an xAI key in the macOS Keychain, then
`video-label process` pre-labels with Grok (`video-label models` lists the models).

## Tests

```sh
.venv/bin/python -m pytest -q
```

## Documentation

- [docs/design.md](docs/design.md): how the app works, the labeling screen, keys, and the
  export format.
- [docs/prelabel-prompt.md](docs/prelabel-prompt.md): the model instruction, the answer
  schema, and the pre-label file format.

## Privacy

Videos stay on your computer. The app sends nothing to a model service: you give the
prepared frames to a model session yourself. Make sure that you are allowed to send the frames of
your videos to a third party before you do.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You can use, change, and share the app for any
noncommercial purpose. Commercial use needs a separate license from the author.

Required Notice: Copyright 2026 giladmanor (https://github.com/giladmanor)
