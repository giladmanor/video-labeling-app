# video-labeling-app

A local macOS app for labeling events in videos quickly and with frame accuracy.

A vision-language model (Grok, through the xAI API) looks at frames of each video and proposes
candidate events. A person then reviews each candidate in a browser-based labeling screen:
accepts or rejects it, fixes the start and end frames, and adds events the model missed.
The app tracks which parts of each video the person actually watched, and exports only those
parts, as new clips with one JSON label file each.

The included prompt and label names target one event type: a driver handling a phone
(`phone` = 1, `driving` = 0, `ignore` = −1).

## Features

- Add one or more video libraries (folders); the app never changes the original files.
- Automatic processing with ffmpeg: playback copies at a constant frame rate, and 2 fps frames
  for the model.
- Model pre-labeling with a schema-checked JSON answer, or candidates imported from a file
  when there is no API key.
- A one-window labeling screen: candidate navigation, play/pause and speed, accept / reject /
  unsure, manual segments, draggable timelines, tags, undo, and keyboard shortcuts.
- Watched-time tracking: only time a person watched is exported.
- Live counts of the training examples that the labels give.
- Export of frame-exact clips, label files, and CSV manifests.

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.11 or newer
- ffmpeg (Homebrew `brew install ffmpeg`; if it is missing, the app downloads a static build)
- An xAI API key for pre-labeling (optional; see "Without an API key")

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

The app starts on http://127.0.0.1:8765/ and opens the browser.

1. **Setup:** enter your name, add a video library folder, and paste your xAI API key
   (stored in the macOS Keychain).
2. **Videos:** click "Scan and process". Each video is normalized, sampled, and pre-labeled.
3. **Label:** open a video. Press `N` for the next suggestion, then `A` (accept), `R` (reject),
   or `U` (unsure), and fix the edges with `I` / `O`. Press `?` for all keys.
   Press `Enter` when a video is done.
4. **Export:** click "Export…". Only videos marked done are exported, and only the parts you
   watched. The export goes to `~/video-labeling/exports/`.

Options: `--workspace DIR` (default `~/video-labeling`), `serve --port N`, `serve --no-browser`.

### Without an API key

Videos wait at the pre-label step. You can still label them by hand, or import candidates
from a JSON file made by another model or a person:

```sh
video-label import-prelabel candidates.json
```

The file format is in [docs/prelabel-prompt.md](docs/prelabel-prompt.md).

### Other commands

```sh
video-label add-library PATH   # add a library folder
video-label process            # process all videos without the server
video-label set-key            # store the xAI API key in the Keychain
video-label models             # list the models your key can use
```

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

Videos stay on your computer. Only the 2 fps frames are sent to the xAI API for pre-labeling.
Make sure that you are allowed to send your videos to a third party before you use it.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You can use, change, and share the app for any
noncommercial purpose. Commercial use needs a separate license from the author.

Required Notice: Copyright 2026 giladmanor (https://github.com/giladmanor)
