# video-labeling-app

A local macOS app for labeling events in videos quickly and with frame accuracy.

## TL;DR

Install:

```sh
curl -fsSL https://raw.githubusercontent.com/giladmanor/video-labeling-app/main/install.sh | sh
```

Run (the first time, open a new terminal or load the new `PATH` first):

```sh
source ~/.zshenv   # first time only; with bash: source ~/.bashrc
video-label        # opens http://127.0.0.1:8765/
```

Uninstall:

```sh
curl -fsSL https://raw.githubusercontent.com/giladmanor/video-labeling-app/main/uninstall.sh | sh
```

See [Install](#install), [Run](#run), and [Uninstall](#uninstall) for details.
The software comes as is, with no warranty; you use it at your own risk (see
[Disclaimer](#disclaimer)).

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

The installer gets everything else. It needs no admin rights, Homebrew, or Xcode tools.

## Install

In a terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/giladmanor/video-labeling-app/main/install.sh | sh
```

The installer:

1. installs [uv](https://docs.astral.sh/uv/) in `~/.local/bin` if it is missing;
2. downloads the app from GitHub (git is not needed);
3. installs the app and its packages with its own Python 3.12 (uv downloads it if needed);
4. downloads a static ffmpeg build if `ffmpeg` and `ffprobe` are not on `PATH`;
5. adds `~/.local/bin` to `PATH` in your shell startup file.

Then open a new terminal and run `video-label`.

To update, run the same line again. To remove the app, see [Uninstall](#uninstall).

Options, as variables in front of `sh`:

- `VIDEO_LABEL_REF=<branch, tag, or commit>`: install that version (default `main`).
- `VIDEO_LABEL_SOURCE=<directory>`: install from a local clone.
- `VIDEO_LABEL_NO_MODIFY_PATH=1`: do not change the shell startup files.

For example: `curl -fsSL …/install.sh | VIDEO_LABEL_REF=v0.2.0 sh`.

### Uninstall

In a terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/giladmanor/video-labeling-app/main/uninstall.sh | sh
```

The uninstaller removes:

1. the app, its packages, and the static ffmpeg build;
2. uv, its cache, its Python downloads, and the `PATH` line, if the installer installed uv.
   If uv was there before, it stays, and only the Python that the installer downloaded is
   removed. If other uv tools are installed, uv and its Python downloads stay; run the
   uninstaller again after you remove those tools.
3. on request, your data: the workspace `~/video-labeling` (playback copies, labels, exports) and the
   xAI key in the Keychain. The uninstaller asks first. The default is to keep the data.

The installer lists what it added in `~/.local/share/video-label/install-receipt`. The
uninstaller removes only those things.

Options, as variables in front of `sh`:

- `VIDEO_LABEL_REMOVE_DATA=1` or `0`: remove or keep the data without asking.
- `VIDEO_LABEL_WORKSPACE=<directory>`: the workspace to remove, if it is not the default.

Your video libraries are never removed.

### Install for development

Needs Python 3.11 or newer and ffmpeg (`brew install ffmpeg`; if it is missing, the app
downloads a static build).

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

### Test videos: label a few videos fully, with no suggestions

Most labels come from time near the model's suggestions. Labels like these are biased: they
miss events that the model also misses, and they have more positives than the real videos.
To get an honest test set, label a few videos with no suggestions at all:

1. Choose a few videos at random, before you pre-label anything. Do not use "Export for
   prelabeling" or `import-prelabel` on them. They can be labeled as soon as they have a
   playback copy.
2. Open each one and watch all of it, from start to end, at 4x or slower. Hold the key of an
   event label while the event plays, or set its edges with `I` / `O`. The timeline shows the
   watched time; `W` plays the next part that you did not watch.
3. Press `Enter` when the full video is watched: the Watched column of the Videos page shows 100%.
4. Write down the video IDs (the first column on the Videos page) and give them to the person
   who trains the model. In the export, these are the `parent_video_id` values. Use these videos
   only for the test set, and to measure what the model misses. Keep all their clips in the
   test set, because clips of one video must not be in two splits.

Start with about one video in five. The export does not mark these videos yet (the `audit`
column is always 0), so the list of IDs is the only record.

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

## Disclaimer

This software is provided "as is", without warranty of any kind, express or implied. This
includes the installer and the uninstaller. There is no warranty that the software, its
installation, or its removal works correctly, is fit for any purpose, or has no effect on your
computer, your files, or your data. You use, install, and remove it at your own risk, and you
alone are responsible for how you use it and for its results, including the labels and
exports that it makes and the frames that you send to a model service. Back up your data.
As far as the law allows, the author is not liable for any damage or loss that comes from
the software or its use. The [License](LICENSE) has the full terms ("No Liability").

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You can use, change, and share the app for any
noncommercial purpose. Commercial use needs a separate license from the author.

Required Notice: Copyright 2026 giladmanor (https://github.com/giladmanor)
