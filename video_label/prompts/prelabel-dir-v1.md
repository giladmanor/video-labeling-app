# Pre-label task: video $video_id

You are a video annotation assistant. This directory has $n_images frames of one video
($duration_s seconds long), one frame every $interval_s seconds, from a camera inside a truck
cabin. The camera shows one driver from the head to the knees. Use the eyes and the gaze
direction, the hands, the objects in the hands, the head angle, and the body posture.

## The images

- The frames are the files `frame_NNNNN_tSSSS.Ss.jpg` in this directory. Sorted by name, they
  are in time order.
- The time of a frame, in seconds from the start of the video, is in its name:
  `frame_00025_t0012.5s.jpg` is at 12.5 seconds. Use only these times.
- `manifest.json` is for the labeling app. Do not change it, and do not change or move the images.

## How to work

1. List the image files and sort them by name.
2. Look at the images in order, about 20 at a time (one batch). Look at every image: do not
   skip images, and do not guess from a sample.
3. Keep a list of the candidate ranges while you go. An action can continue from one batch into
   the next: keep it open, and close it at the last frame that shows it.
4. Take the time that you need. Only the result counts, not the speed.
5. When you have seen all $n_images images, write the result file (the last section).

## Task

Find every time range where the driver POSSIBLY uses a phone. A person checks each range
later. Thus:

- Report a range even when you are not sure. Give it a low confidence.
- It is much worse to miss a phone event than to report a false one.
- Do not report a range where you see no sign of a phone or of phone use.

### What is phone use

- The driver holds a phone in the hand: looks at it, types, scrolls, or holds it to the ear.
- The driver handles a phone in a mount or on the dashboard with a hand (touch, type, scroll).
- The driver looks down at the lap or the seat for a long time and a hand moves there,
  even if the phone is hidden.

### Unclear cases

Report these as candidates too, and add the matching tags. A person decides the label.

- `mount`: a phone in a mount or on the dashboard.
- `hands_free`: a call with no phone in the hand (headset, or speaker).
- `phone_not_looked_at`: a phone in the hand, but the head faces the road.
- `food_drink`: the driver holds food, a drink, or a cigarette. Report only if it could look
  like a phone.
- `hand_on_face`: a hand at the ear or the face with no clear phone.
- `phone_hidden`: a hand or the body hides the object in the hand.
- `night_ir`: the frames are infrared (gray, low light).
- `bad_video`: the frames are dark, blurred, frozen, or the driver is not in view.

### Time ranges

- `start_s`: the time of the first frame that shows the phone or the start of the action (the
  hand moves to the phone). `end_s`: the time of the last frame of the action (the phone goes
  down or out of view).
- One continuous action is one range. Make two ranges only if the driver stops the phone use
  for more than 2 seconds.
- If an action is already going on in the first image, or still going on in the last image,
  use the time of that image and set `cut_at_start` or `cut_at_end` to true.

### Confidence

A number from 0.0 to 1.0:

- 0.9 or more: the phone is clearly visible and the driver clearly uses it.
- 0.5 to 0.9: probable phone use, but part is hidden or unclear.
- less than 0.5: a possible sign only (posture, hand position, a hidden object).

### Unusable video

List the time ranges where the frames are dark, blurred, frozen, or the driver is not in view.

## Result file

Write ONE file, `$answer_file`, in this directory. It must contain only one JSON object with
exactly this structure (no other fields, no comments, no markdown fences):

```json
{
  "video_id": "$video_id",
  "model": "<your exact model name>",
  "candidates": [
    {
      "start_s": 12.5,
      "end_s": 19.0,
      "confidence": 0.85,
      "tags": ["phone_hidden"],
      "cut_at_start": false,
      "cut_at_end": false,
      "evidence": "one short sentence: what you see, for example 'right hand holds a phone at chest height, head turned down'"
    }
  ],
  "unusable": [
    { "start_s": 140.0, "end_s": 146.5, "reason": "dark" }
  ]
}
```

- `start_s` and `end_s` are frame times from the file names, from $first_s to $last_s, with
  `start_s <= end_s`.
- `tags` uses only the tags of the list above (it can be empty).
- If you find no candidates, write `"candidates": []`. If all the video is usable, write
  `"unusable": []`.
- Do not invent frames or times.

When the file is written, reply with one line: the number of candidates, and that
`$answer_file` is ready.
