# Design

## 1. What the app does

The app helps a person label events in a library of videos quickly and with frame accuracy.
A vision-language model (Grok, through the xAI API) proposes candidate events, and a person
confirms them and marks the exact frames. The result is a new dataset that contains only the
video that a person watched, with one label file for each clip.

The steps:

1. Ask the user where the video libraries are (one or more directories).
2. Connect to the xAI API with the user's key.
3. Make playback copies of the videos with ffmpeg and extract frames at 2 fps.
4. Send the frames to the model and get candidate events (`docs/prelabel-prompt.md`).
5. Run the labeling screen: the person checks the candidates and fixes the frames.
   The screen shows how many training examples the labels give.
6. Export: cut the watched parts of the videos into new clips and write the label files.

The app ships with one event type: a driver who handles a phone. The event definition is in
the pre-label prompt (`video_label/prompts/prelabel-v1.txt`) and the label names.

### Labels

| label | value | meaning |
|---|---|---|
| `phone` | 1 | the event is visible |
| `driving` | 0 | no event |
| `ignore` | −1 | unclear; not used for training |
| unknown | – | time that nobody watched; never exported |

## 2. Requirements

- **macOS** (Apple Silicon or Intel). No admin rights necessary.
- **Local data.** The videos stay on the computer. Only the 2 fps frames go to the xAI API.
  Make sure that you are allowed to send the frames of your videos to a third party.
- **Read-only libraries.** The app never changes, moves, or deletes the library files.
- **Frame-accurate.** Segment boundaries are correct to one frame.
- **Safe and resumable.** Each save is atomic. A crash or a restart loses no work.
  The processing jobs continue after a restart.
- **Only confirmed labels.** The export contains only time that a person watched, and only
  labels that a person confirmed.

## 3. Install and run

See the README. The command `video-label` starts a local server on `127.0.0.1:8765` and
opens the browser. All screens (setup, videos, labeling, export) are pages of this local app.

Commands:

| command | what it does |
|---|---|
| `video-label [serve] [--port N] [--no-browser]` | start the app (default) |
| `video-label add-library PATH` | add a video library directory |
| `video-label process` | scan and process all videos without the server |
| `video-label import-prelabel FILE` | use candidates from a JSON file instead of Grok |
| `video-label set-key` | store the xAI API key in the macOS Keychain |
| `video-label models` | list the models that the key can use |

All commands take `--workspace DIR` (default `~/video-labeling`, or `VIDEO_LABEL_WORKSPACE`).

### 3.1 ffmpeg

The app looks for ffmpeg and ffprobe in this order:

1. the `VIDEO_LABEL_FFMPEG` and `VIDEO_LABEL_FFPROBE` variables;
2. the binaries on `PATH` (for example Homebrew);
3. the `static-ffmpeg` Python package, which downloads a pinned static build on first use.

The setup page shows the version. If several people label the same videos, they must use the
same ffmpeg version, because the label frame numbers refer to the normalized copies.

## 4. Setup page

1. **Labeler name.** It goes into each label file and each export.
2. **Video libraries.** Choose a folder with the native macOS dialog, or paste a path.
   macOS can ask once for access to Desktop, Documents, Downloads, or an external disk; if
   access is denied, the scan finds no videos. The app scans each directory recursively for
   `.mp4`, `.mov`, `.avi`, `.mkv`, and `.m4v` files, and scans again at each start.
3. **Grok.** Paste an xAI API key (from `console.x.ai`; a grok.com or X subscription does not
   give API access). The app tests the key and stores it in the macOS Keychain, never in a
   file. `XAI_API_KEY` in the environment overrides the Keychain. Choose the model; the page
   lists the models that the key can use.

## 5. Processing jobs

```
scan -> hash -> probe -> normalize -> frames (2 fps) -> prelabel -> ready to label
```

- **hash.** `sha256` of the file. `video_id` is the first 12 hex characters, so the same file
  gets the same ID on every computer.
- **probe.** Duration, fps, resolution, codec. Videos that fail show the error.
- **normalize.** A playback copy: H.264, constant 15 fps, maximum height 720, one keyframe
  each second, no audio. Browsers cannot play all codecs, and the short keyframe interval
  makes seeking fast. All label frame numbers refer to this copy.
- **frames.** 2 fps JPEG frames, 768 pixels high, for the model only. They are deleted after
  the prelabel step (a setting).
- **prelabel.** Model calls as in `docs/prelabel-prompt.md`. Without an API key, a video stops
  at this step until a key is set, or until candidates are imported with `import-prelabel`.

The Videos page lists each video with its step and status. A video can be labeled as soon as
its prelabel step is done; the jobs continue in the background.

### Workspace layout

```
~/video-labeling/
  config.json                  # libraries, labeler, model, settings (no API key)
  state.sqlite                 # videos and job status
  videos/{video_id}.mp4        # normalized playback copies (about 0.5-1 GB per hour)
  frames/{video_id}/...        # 2 fps frames, deleted after the prelabel step
  prelabels/{run_id}/{video_id}.json
  labels/{video_id}.json       # the labels of this person, one file for each video
  exports/{export_name}/       # section 8
```

## 6. Labeling screen

The screen fits one window:

- **Top bar:** video name, frame, watched percent, status (click it to reopen a video that is
  done or skipped), and the video actions: Unwatched, Random, Done, Skip, Export, help.
- **Left of the video:** previous / next suggestion and its details (time, confidence,
  decision, what the model saw), Play/Pause, speed, and the decision on the suggestion:
  Accept → phone (1), Reject → driving (0), Unsure → ignore (−1).
- **Right of the video:** new phone segment (hold, or start/end), the selected segment
  (set its label to phone / driving / ignore, delete, undo, tags), the training-examples
  counts, and a note for the video.
- **Two timelines** (zoomed and full video) with tracks for the suggestions, the labels, and
  the watched time. Drag on a timeline to move the playhead. A color legend is below them.

### 6.1 Keys

| key | action |
|---|---|
| `Space` | play or pause |
| `←` `→` | one frame back or forward |
| `Shift` + `←` `→` | one second back or forward |
| `[` `]` | slower or faster (0.5x to 4x) |
| `N` / `Shift`+`N` | next / previous suggestion, played with 2 s before and 1 s after |
| `A` | accept the suggestion: it becomes a phone segment |
| `R` | reject the suggestion: it becomes a driving segment (a hard negative) |
| `U` | the suggestion or the selected segment becomes ignore |
| `1` / `0` | set the selected segment to phone / driving |
| `P` (hold) | mark phone while the key is down (for events the model missed) |
| `I` / `O` | set the start / end of the selected segment, or of a new phone segment |
| `Delete` | delete the selected segment |
| `T` then `1`–`8` | toggle a tag on the selected segment |
| `W` | play the next unwatched part |
| `B` | play a random unwatched part (for normal examples) |
| `⌘Z` | undo |
| `Enter` | mark the video done and open the next one |
| `Shift`+`S` | skip the video (the note is the reason) |
| `Esc` | clear the selection |
| `?` | show the keys |

### 6.2 Watched time

- The app records each time range that the person played at 4x or slower, or stepped through
  frame by frame, while the browser tab was visible.
- At export, watched time with no segment becomes `driving` (0).
- Time that nobody watched is never labeled and never exported.

### 6.3 Start frame rule

A phone segment starts at the first frame where the event is visible: the phone is in view in
the hand, or the hand clearly moves to take it. All labelers must use the same rule.

### 6.4 Decisions

The app records the decision for each suggestion (`accepted`, `rejected`, `unsure`). This
measures the precision of the model and helps to tune its confidence threshold.

## 7. Training-examples panel

The panel counts the training samples that the confirmed labels give, with a window config.
The default config is `causal-2s`, for a model that decides on a live stream with 2 seconds of
history:

```yaml
name: causal-2s
length_s: 2.0          # the history that the model sees
stride_s: 0.5
label_rule: end        # the label is the state at the end of the window ("now")
end_part_s: 0.5        # the "now" part: the last 0.5 s of the window
positive_if: phone fraction of the end part >= 0.5
negative_if: no phone and no ignore frame in the end part
otherwise: ignore
```

The panel shows positive, negative, and ignored windows, distinct events, hard negatives,
source videos, and watched time, for the current video and for all videos. Windows from one
event overlap and are very similar, so the number of events and of source videos is a better
measure of the dataset size than the number of windows.

## 8. Export

### 8.1 Steps

1. Take the videos with the status "done". (If there are none, the export stops with a message.)
2. Each watched range of a video becomes one clip.
3. Drop clips shorter than one window length; the export summary shows how many.
4. Cut each clip from the normalized video by frame number and encode it again (H.264,
   CRF 18). The labels are frame numbers on the normalized video, so this keeps them exact.
   A stream copy would cut at keyframes and add unwatched frames.
5. Move the segments to clip frame numbers and fill watched gaps with `driving`.
6. Write the label files and the manifests.

### 8.2 Layout

```
exports/{labeler}_{date-time}/
  export.json                   # app and ffmpeg version, model run, window config, counts
  videos/{clip_id}.mp4          # clip_id = {video_id}_{start_frame:06d}
  labels/{clip_id}.json         # one label file for each clip
  manifests/
    videos.csv                  # one row for each clip
    segments.csv                # one row for each segment
```

`videos.csv` columns: `video_id`, `source_file`, `sha256`, `fps`, `n_frames`, `duration_s`,
`width`, `height`, `group_id`, `label_type`, `parent_video_id`, `parent_sha256`,
`clip_start_s`, `audit`, `labeler`.

`segments.csv` columns: `video_id`, `start_s`, `end_s`, `start_frame`, `end_frame`, `label`,
`source`, `origin`, `tags`.

### 8.3 Label file

```json
{
  "clip_id": "3f2a9c01b7e4_000180",
  "parent_video_id": "3f2a9c01b7e4",
  "parent_sha256": "3f2a9c01b7e4…",
  "parent_file": "batch1/video_0412.avi",
  "library": "library-1",
  "group_id": "3f2a9c01b7e4",
  "clip_start_s": 12.0,
  "clip_start_frame": 180,
  "fps": 15,
  "n_frames": 300,
  "duration_s": 20.0,
  "labeler": "dana",
  "audit": false,
  "labeled_at": "2026-11-11T10:02:00Z",
  "label_names": { "1": "phone", "0": "driving", "-1": "ignore" },
  "segments": [
    { "start_frame": 0,  "end_frame": 44,  "label": 0, "tags": [], "origin": "watched" },
    { "start_frame": 45, "end_frame": 141, "label": 1, "tags": ["phone_hidden"],
      "origin": "grok_accepted", "candidate_id": "c001", "candidate_confidence": 0.85 },
    { "start_frame": 142, "end_frame": 299, "label": 0, "tags": [], "origin": "watched" }
  ]
}
```

- `parent_file` is relative to the library root. No personal paths go into the export.
- `origin`: `grok_accepted`, `grok_rejected`, `manual` (an event the model missed),
  `watched` (the fill), or `unsure`.
- The segments cover every frame of the clip with no gap and no overlap.
- `group_id` is the parent video. Keep all clips of one group in the same data split
  (train, validation, or test), so that the splits do not share source video.

## 9. Things to know when you train on the export

1. **The model selects most positives.** Events that the model misses are rare in the export.
   Use `P` for missed events, and fully watch some videos to measure what the model misses.
2. **The class balance is not the real one.** People watch mostly the time near suggestions,
   so the export has more positives than the real videos. Choose thresholds on fully watched
   videos.
3. **Few normal examples.** Use `B` to watch random parts of each video.
4. **Short clips lose context.** A clip shorter than one window gives no sample. Candidate
   playback starts 2 s before the suggestion for this reason.
5. **Boundaries.** The model gives times in 0.5 s steps. Step the start edge frame by frame
   and follow the start frame rule.
6. **Anchoring.** Labelers see the suggestions and can copy them. Compare with fully watched
   videos.
7. **Short events at 2 fps.** The model sees one frame each 0.5 s, so very short events can
   fall between frames.

## 10. Code

```
pyproject.toml           # package "video-label", command video-label
video_label/
  cli.py                 # commands (section 3)
  server.py              # FastAPI: pages, video files with range requests, labels, export
  workspace.py           # config.json, state.sqlite, directories, atomic JSON write
  ffmpeg.py              # find ffmpeg, probe, normalize, frames
  jobs.py                # scan, hash, probe, normalize, frames, prelabel; background worker
  grok.py                # xAI client, macOS Keychain
  prelabel.py            # chunks -> model -> schema check -> merge -> prelabels/
  store.py               # label files, validation, watched ranges, fill
  windows.py             # window counts for the panel
  export.py              # watched ranges -> clips, label files, manifests
  prompts/prelabel-v1.txt  # the system instruction of docs/prelabel-prompt.md
  static/                # index.html, app.js, style.css
tests/                   # pytest: logic, and an end-to-end run with a fake model
```

Not built yet: audit videos (fully watched videos with the suggestions hidden at first), a cost
estimate before the model step, a label guide page, and `.app` packaging.
