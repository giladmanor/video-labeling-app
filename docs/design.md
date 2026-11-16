# Design

## 1. What the app does

The app helps a person label events in a library of videos quickly and with frame accuracy.
A vision-language model proposes candidate events, and a person confirms them and marks the
exact frames. The app has no model connection: it prepares the frames and the instruction for
a chat model that the person uses by hand (section 5.1), and imports the answers. The result is a new dataset that contains only the
video that a person watched, with one label file for each clip.

The steps:

1. Ask the user where the video libraries are (one or more directories).
2. Make playback copies of the videos with ffmpeg.
3. Prepare 2 fps frames and the instruction for a chat model; import the candidate events
   that the person pastes back (`docs/prelabel-prompt.md`).
4. Run the labeling screen: the person checks the candidates and fixes the frames.
   The screen shows how many training examples the labels give.
5. Export: cut the watched parts of the videos into new clips and write the label files.

The app ships with one event type: a driver who handles a phone. The event definition is in
the pre-label prompt (`video_label/prompts/prelabel-v1.txt`) and the label names.

### Labels

The labels are set on the Setup page (`labels` in `config.json`). A new workspace starts with
these seed labels:

| label | value | key | color | meaning |
|---|---|---|---|---|
| `phone` | 1 | `1` | `#d9480f` | the event is visible |
| `driving` | 0 | `0` | `#2f9e44` | no event |
| `ignore` | −1 | `U` | `#868e96` | unclear; not used for training |
| unknown | – | – | – | time that nobody watched; never exported |

- **The value** goes into the label files and the export. The app gives it: a new event label
  gets the next value from 1 that no label used before. It cannot be changed.
- **The key** is any one character that is not already a key of the labeling screen
  (`Space A N R I O W B T S P ? [ ]`; ignore may use `U`). On the labeling screen it sets the
  selected segment to this label.
- **The color** is the color of the label on the labeling screen (buttons, timeline, legend).
  A new label gets the first palette color that no label uses; click the color on the Setup
  page to choose another one. Text on a colored button is black or white, whichever reads better.
- **Event labels** have values from 1. The first event label is the default: Accept, `P`, and
  Start/End segment (`I`/`O`) make it.
- **0 is the background.** Watched time with no segment gets it, and a rejected suggestion
  becomes it. It always exists; only its name can change.
- **−1 is ignore.** It always exists; only its name can change.
- A label that is used in a label file cannot be removed. Names are 1 to 32 letters, digits,
  `_`, or `-`.
- The pre-label prompt does not change with the labels. Edit it, or write a new prompt version,
  for a new event type.

## 2. Requirements

- **macOS** (Apple Silicon or Intel). No admin rights necessary.
- **Local data.** The videos stay on the computer. The app sends nothing to a model service;
  the person uploads the prepared frames. Make sure that you are allowed to send the frames of
  your videos to a third party.
- **Read-only libraries.** The app never changes, moves, or deletes the library files.
- **Frame-accurate.** Segment boundaries are correct to one frame.
- **Safe and resumable.** Each save is atomic. A crash or a restart loses no work.
  The processing jobs continue after a restart.
- **Only confirmed labels.** The export contains only time that a person watched, and only
  labels that a person confirmed.

## 3. Install and run

See the README. The one-line installer is [install.sh](../install.sh): it installs uv, the app
with its own Python, and (if ffmpeg is not on `PATH`) the static ffmpeg build of §3.1. The command `video-label` starts a local server on `127.0.0.1:8765` and
opens the browser. All screens (setup, videos, labeling, export) are pages of this local app.

Commands:

| command | what it does |
|---|---|
| `video-label [serve] [--port N] [--no-browser]` | start the app (default) |
| `video-label add-library PATH` | add a video library directory |
| `video-label process` | scan and process all videos without the server |
| `video-label import-prelabel FILE` | use candidates from a JSON file |
| `video-label set-key` | store an xAI API key in the macOS Keychain (Grok, CLI only) |
| `video-label models` | list the models that the key can use |

All commands take `--workspace DIR` (default `~/video-labeling`, or `VIDEO_LABEL_WORKSPACE`).

### 3.1 ffmpeg

The app looks for ffmpeg and ffprobe in this order:

1. the `VIDEO_LABEL_FFMPEG` and `VIDEO_LABEL_FFPROBE` variables;
2. the binaries on `PATH` (for example Homebrew);
3. the `static-ffmpeg` Python package, pinned to 3.0 in `pyproject.toml` (ffmpeg 7.0), which
   downloads its static build on first use (the installer downloads it at install time).

The setup page shows the version. If several people label the same videos, they must use the
same ffmpeg version, because the label frame numbers refer to the normalized copies.

## 4. Setup page

1. **Labeler name.** It goes into each label file and each export.
2. **Video libraries.** Choose a folder with the native macOS dialog, or paste a path.
   macOS can ask once for access to Desktop, Documents, Downloads, or an external disk; if
   access is denied, the scan finds no videos. The app scans each directory recursively for
   `.mp4`, `.mov`, `.avi`, `.mkv`, and `.m4v` files, and scans again at each start.
3. **Labels.** The label list (section 1). Add event labels, rename labels, and save.

## 5. Processing jobs

```
scan -> hash -> probe -> normalize -> ready to label and to pre-label by hand (5.1)
```

- **hash.** `sha256` of the file. `video_id` is the first 12 hex characters, so the same file
  gets the same ID on every computer.
- **probe.** Duration, fps, resolution, codec. Videos that fail show the error.
- **normalize.** A playback copy: H.264, constant 15 fps, maximum height 720, one keyframe
  each second, no audio. Browsers cannot play all codecs, and the short keyframe interval
  makes seeking fast. All label frame numbers refer to this copy.
- **Grok (command line only).** `video-label process` with an xAI key (`video-label set-key`
  or `XAI_API_KEY`) also runs two more steps: **frames** (2 fps JPEG frames for the model,
  deleted after the next step) and **prelabel** (model calls as in `docs/prelabel-prompt.md`).
  The app does not run them; it deletes frames that an older version left.

The Videos page lists each video with its step and status, and the buttons "Label" and
"Export for prelabeling" (the Prepare prelabel page, section 5.1). A video can be labeled as soon as
its prelabel step is done; the jobs continue in the background.

The top bar has the tabs Setup and Videos. The tabs Prepare prelabel and Label belong to one
video: they show only after a video is selected with one of its buttons, next to the file name
of that video. The app opens the Setup page while the labeler name or a video library is
missing, with a breathing glow on the next setting to fill in; otherwise it opens Videos.

### Workspace layout

```
~/video-labeling/
  config.json                  # libraries, labeler, model, settings (no API key)
  state.sqlite                 # videos and job status
  videos/{video_id}.mp4        # normalized playback copies (about 0.5-1 GB per hour)
  frames/{video_id}/...        # 2 fps frames, deleted after the prelabel step
  prelabels/{run_id}/{video_id}.json
  manual-prelabel/{video_id}_{date-time}/  # frames and prompt for a chat model used by hand (5.1)
  labels/{video_id}.json       # the labels of this person, one file for each video
  exports/{export_name}/       # section 8
```

### 5.1 Prepare prelabel (a labeling LLM used by hand)

The "Prepare prelabel" page is for a model with no API connection: a chat or agent session (for
example Claude) that can read a local directory. It is for the selected video. If the video
already has an imported run, an orange notice says so; a new import replaces it.

The page is a wizard with three steps. A done step collapses to a button that opens it again:

1. **Prepare directory.** The main button. "Edit README.md" shows the frame rate, the frame
   height, and the README for the model. After it, the step is "Prepare directory again".
2. **The instruction.** "Samples prepared for prelabel: 134 images · 2 fps", the directory, and
   the instruction to copy. After an import: "Generate instruction again".
3. **Import.** The state of `prelabels.json` and the import button. After an import: "Import again".

The page shows the newest prepared directory of the selected video. Another video starts the page
fresh. In step 1, choose the frame rate and
the frame height, and edit the README if necessary. "Prepare directory" writes all the frames of
the video into one directory:

```
manual-prelabel/{video_id}_{date-time}/
  README.md         # the task, how to look at the frames, and the result format
  manifest.json     # for the app
  frame_00001_t0000.0s.jpg ... frame_00134_t0066.5s.jpg   # the time is in the name
  prelabels.json    # written by the model
```

The page shows the directory and a short instruction to paste into the model session. The
instruction points to `README.md`, where most of the instructions are: look at every image in
order, about 20 at a time, at the model's own pace; keep a running list of candidates; then
write `prelabels.json`:

```json
{"video_id": "...", "model": "...", "candidates": [...], "unusable": [...]}
```

`candidates` and `unusable` use the schema of `docs/prelabel-prompt.md` section 4, with the
times of the file names. The page looks for the file every 5 seconds and checks it (the
structure, the tags, the times inside the video). "Import" makes it the pre-label run
`{model}_{prompt_version}` of the video, with the model name from the file unless one is typed
on the page.

The README is the template `video_label/prompts/prelabel-dir-v1.md`, with `$video_id`,
`$n_images`, `$interval_s`, `$duration_s`, `$first_s`, `$last_s`, and `$answer_file` filled in.
If it was changed on the page, the prompt version gets the suffix `-custom`.

## 6. Labeling screen

The screen fits one window:

- **Top bar:** video name, frame, watched percent, status (click it to reopen a video that is
  done or skipped), and the video actions: Unwatched, Random, Done, Skip, help.
- **Left of the video:** previous / next suggestion and its details (time, confidence,
  decision, what the model saw), Play/Pause, speed, and the decision on the suggestion:
  Accept → the first event label, Reject → background (0), Unsure → ignore (−1).
- **Right of the video:** new segment (Start segment / End segment, then one Hold button for
  each label: hold it while the video plays to mark that label), the selected segment (one
  button for each label, with its key; delete, undo, tags), the training-examples counts, and
  a note for the video.
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
| `A` | accept the suggestion: it becomes a segment of the first event label |
| `R` | reject the suggestion: it becomes a background (0) segment (a hard negative) |
| `U` | the suggestion or the selected segment becomes ignore |
| label key | set the selected segment to this label (Setup page; seed `1` phone, `0` driving) |
| `P` (hold) | mark the first event label while the key is down (for events the model missed) |
| `I` / `O` | set the start / end of the selected segment, or of a new event segment |
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
- At export, watched time with no segment becomes the background label (0).
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
positive_if: event fraction (any label from 1) of the end part >= 0.5
negative_if: no event and no ignore frame in the end part
otherwise: ignore
```

The panel shows positive, negative, and ignored windows, distinct events (also for each event
label when there are several), hard negatives,
source videos, and watched time, for the current video and for all videos. Windows from one
event overlap and are very similar, so the number of events and of source videos is a better
measure of the dataset size than the number of windows.

## 8. Export

The export is on the Videos page, in the "Training data" card. The card shows what an export
would hold now: the seconds of watched time (in parts at least one window long), and a table with
each label, its key and value, its number of instances (segments, after the watched gaps are
filled with the background label), and its seconds. "Export" writes the export and shows the
output directory, with "Show in Finder".

### 8.1 Steps

1. Take every video with watched time, whatever its status, except the videos marked "skip".
   Watched time is seen and checked by a person, so a video does not need to be "done".
   (If there is no watched time, the export stops with a message.)
2. Each watched range of a video becomes one clip.
3. Drop clips shorter than one window length; the export summary shows how many.
4. Cut each clip from the normalized video by frame number and encode it again (H.264,
   CRF 18). The labels are frame numbers on the normalized video, so this keeps them exact.
   A stream copy would cut at keyframes and add unwatched frames.
5. Move the segments to clip frame numbers and fill watched gaps with the background label (0).
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
- `label_names` comes from the label list of the Setup page.
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
  grok.py                # xAI client, macOS Keychain (command line only)
  prelabel.py            # chunks -> model -> schema check -> merge -> prelabels/
  manual.py              # pre-label files for a chat model used by hand (section 5.1)
  store.py               # label files, validation, watched ranges, fill
  windows.py             # window counts for the panel
  export.py              # watched ranges -> clips, label files, manifests
  prompts/prelabel-v1.txt  # the system instruction of docs/prelabel-prompt.md
  static/                # index.html, app.js, style.css
tests/                   # pytest: logic, and an end-to-end run with a fake model
```

Not built yet: audit videos (fully watched videos with the suggestions hidden at first), a cost
estimate before the model step, a label guide page, and `.app` packaging.
