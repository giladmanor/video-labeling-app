# Pre-label instruction for a vision-language model

Version `prelabel-v1`. The labeling app reads the result of this step (`docs/design.md`).

## 1. Purpose

A vision-language model (for example Grok) looks at each video and finds the time ranges
where the driver possibly uses a phone. These ranges are **candidates**, not labels.
A person checks each candidate in the labeling tool, accepts or rejects it, and fixes the
start and end frames.

Thus the model must find almost all events (high recall). False candidates are acceptable,
because a person rejects them in a few seconds. A missed event costs more, because no person
looks for it.

## 2. How the script sends the video

Most vision-language APIs accept images, not long videos. Thus the labeling app
(`video_label/prelabel.py`) does these steps for each normalized video. The app connects
with the xAI API key of the labeler, stored in the macOS Keychain.

1. Extract frames at 2 fps, scaled to 768 pixels high.
2. Cut the frame list into chunks of 30 seconds (60 frames), with 4 seconds of overlap.
   Change the chunk size if the API has a lower image limit.
3. Send one request for each chunk: the system instruction (section 3), then for each frame
   a text line `t=<seconds>` and the image. The times are seconds from the start of the
   full video, with one decimal.
4. Read the JSON answer (section 4). Check it against the schema. If the check fails,
   send the request again one time, then record the chunk as failed.
5. Merge the chunks: join candidates that overlap or have a gap of 1 second or less.
   Keep the highest confidence and the union of the tags.
6. Write the pre-label file (section 5).

If the API accepts a video file directly, send the chunk as a video and keep the same
instruction. The model then gives the times from the video start, and the script adds
the chunk start time.

**Data permission.** This step sends video frames to xAI. Make sure that you are allowed to
do this with your videos.

**Without an API key.** Candidates can come from any other source (another model, or a
person) in a JSON file with `video_id`, `model`, `candidates`, and `unusable` (the fields of
section 4). Load it with `video-label import-prelabel FILE`. The same check applies.

**A labeling LLM used by hand.** The "Prepare prelabel" page of the app writes all the 2 fps
frames of a video into one directory (the time is in each file name), with a `README.md` that
has this task, how to look at every frame, and the result format
(`video_label/prompts/prelabel-dir-v1.md`). A model session that can read the directory writes
`prelabels.json` there (the answer schema of section 4, plus `video_id` and `model`), and the
app imports it with the same check (`video_label/manual.py`, `docs/design.md` section 5.1).

## 3. System instruction (send as is)

```text
You are a video annotation assistant. You look at frames from a camera inside a truck
cabin. The camera shows one driver from the head to the knees. Use the eyes and the
gaze direction, the hands, the objects in the hands, the head angle, and the body posture.

Each frame comes after a line "t=<seconds>". The time is in seconds from the start of the
video. The frames are 0.5 seconds apart.

TASK
Find every time range where the driver POSSIBLY uses a phone. A person will check each
range later. Thus:
- Report a range even when you are not sure. Give it a low confidence.
- It is much worse to miss a phone event than to report a false one.
- Do not report a range where you see no sign of a phone or of phone use.

WHAT IS PHONE USE (label "phone")
- The driver holds a phone in the hand: looks at it, types, scrolls, or holds it to the ear.
- The driver handles a phone in a mount or on the dashboard with a hand (touch, type, scroll).
- The driver looks down at the lap or the seat for a long time and a hand moves there,
  even if the phone is hidden.

UNCLEAR CASES
Report these as candidates too, and add the matching tags. A person decides the label.
- "mount": a phone in a mount or on the dashboard.
- "hands_free": a call with no phone in the hand (headset, or speaker).
- "phone_not_looked_at": a phone in the hand, but the head faces the road.
- "food_drink": the driver holds food, a drink, or a cigarette. Report only if it could
  look like a phone.
- "hand_on_face": a hand at the ear or the face with no clear phone.
- "phone_hidden": a hand or the body hides the object in the hand.
- "night_ir": the frames are infrared (gray, low light).
- "bad_video": the frames are dark, blurred, frozen, or the driver is not in view.

TIME RANGES
- start_s: the time of the first frame that shows the phone or the start of the action
  (the hand moves to the phone). end_s: the time of the last frame of the action
  (the phone goes down or out of view).
- Use only the times that are in the frame captions.
- One continuous action is one range. Make two ranges only if the driver stops the phone
  use for more than 2 seconds.
- An action can start before the first frame or end after the last frame. Then use the
  time of the first or the last frame, and set "cut_at_start" or "cut_at_end" to true.

CONFIDENCE
A number from 0.0 to 1.0:
- 0.9 or more: the phone is clearly visible and the driver clearly uses it.
- 0.5 to 0.9: probable phone use, but part is hidden or unclear.
- less than 0.5: a possible sign only (posture, hand position, a hidden object).

OUTPUT
Reply with ONLY one JSON object, with no text before or after it and no markdown fences.
Use this exact structure:
{
  "candidates": [
    {
      "start_s": <number>,
      "end_s": <number>,
      "confidence": <number 0.0 to 1.0>,
      "tags": [<zero or more tags from the list above>],
      "cut_at_start": <true or false>,
      "cut_at_end": <true or false>,
      "evidence": "<one short sentence: what you see, for example 'right hand holds a phone at chest height, head turned down'>"
    }
  ],
  "unusable": [
    { "start_s": <number>, "end_s": <number>, "reason": "<dark, blurred, frozen, driver not in view>" }
  ]
}
If you find no candidates, reply {"candidates": [], "unusable": []}.
Do not invent frames or times. Do not add other fields.
```

## 4. Chunk answer schema (for the check in step 4)

```json
{
  "type": "object",
  "required": ["candidates", "unusable"],
  "additionalProperties": false,
  "properties": {
    "candidates": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["start_s", "end_s", "confidence", "tags", "cut_at_start", "cut_at_end", "evidence"],
        "additionalProperties": false,
        "properties": {
          "start_s": { "type": "number", "minimum": 0 },
          "end_s": { "type": "number", "minimum": 0 },
          "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
          "tags": {
            "type": "array",
            "items": { "enum": ["mount", "hands_free", "phone_not_looked_at", "food_drink",
                                "hand_on_face", "phone_hidden", "night_ir", "bad_video"] }
          },
          "cut_at_start": { "type": "boolean" },
          "cut_at_end": { "type": "boolean" },
          "evidence": { "type": "string" }
        }
      }
    },
    "unusable": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["start_s", "end_s", "reason"],
        "additionalProperties": false,
        "properties": {
          "start_s": { "type": "number", "minimum": 0 },
          "end_s": { "type": "number", "minimum": 0 },
          "reason": { "type": "string" }
        }
      }
    }
  }
}
```

The script also checks that `start_s <= end_s` and that both times are inside the chunk.
If the API supports a JSON-schema output mode, give it this schema too.

## 5. Pre-label file (the labeling tool reads this)

Path: `prelabels/{run_id}/{video_id}.json` in the workspace of the labeling app.
The `run_id` names the model and the
instruction version, for example `grok-prelabel-v1-20261111`.

```json
{
  "video_id": "v000123",
  "run_id": "grok-prelabel-v1-20261111",
  "model": "<the exact model name from the API>",
  "prompt_version": "prelabel-v1",
  "sample_fps": 2,
  "created_at": "2026-11-11T10:02:00Z",
  "covered": [[0.0, 180.0]],
  "failed_chunks": [],
  "candidates": [
    {
      "id": "c001",
      "start_s": 12.5,
      "end_s": 19.0,
      "confidence": 0.85,
      "tags": ["phone_hidden"],
      "evidence": "right hand holds a dark object at the lap, head turned down"
    }
  ],
  "unusable": [
    { "start_s": 140.0, "end_s": 146.5, "reason": "dark" }
  ]
}
```

- `covered` lists the time that the model saw with no error. `failed_chunks` lists the rest.
- The times are in seconds on the normalized video. The frames are 0.5 seconds apart, so a
  boundary can be wrong by up to 0.5 seconds. The tool adds 1 second of padding to each
  side when it plays a candidate, and the person fixes the exact frames.
- Only the labeling screen and the export make labels. The pre-label file only guides the person.

## 6. Check the pre-labels before you trust them

On videos that a person watched fully, with the candidates hidden at first, measure:

- **recall**: the part of the phone segments of the person that a candidate overlaps.
  Target 95% or more.
- **start error**: the candidate start minus the start frame of the person. A late start
  makes the labeler copy a late start.
- **candidates per minute** of video: this sets the review time for the labelers.
- the confidence threshold that keeps the recall target with the fewest candidates.

If the recall is low, change the instruction (new `prompt_version`, a new file in
`video_label/prompts/`), the frame rate, or the chunk size, and measure again.
