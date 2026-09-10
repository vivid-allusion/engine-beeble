# User guide — SwitchX best practices

Same guidance as Beeble's own docs (SwitchX 1.0 — the model the API exposes),
adapted for this Engine. Two concepts drive every result: the **alpha mask**
controls *what* gets changed; the **reference image** controls *how* it looks.

## 1. Alpha mask modes (what gets changed)

| Mode | When to use |
|------|-------------|
| `auto` | **Background replacement, relighting, virtual production.** Engine detects the subject in the first frame and tracks it through the video. No mask needed — the default, start here. |
| `fill` | Full-frame relighting/styling: keep the scene's geometry, change lighting/mood. No mask. |
| `select` | Targeted changes (new outfit, inpainting). Provide a **single keyframe** alpha image via the `alpha_uri` slot; SwitchX propagates it across the clip. Use `alpha_keyframe_index` to pick a non-first reference frame. |
| `custom` | **Pixel-perfect control.** Provide a full per-frame alpha matte (Nuke/AE output) via the `alpha_uri` slot. Matches the cloud app's "Upload" mode. |

For clean selections, mask distinct parts (e.g. face + hands) as separate
regions rather than one blob; an invert option exists for targeted swaps.

## 2. Camera tracking caveat

SwitchX infers camera motion from the **unmasked foreground only**. Markers or
parallax in a masked background are invisible to it. Expect good tracking when
the foreground has rich motion (a walking subject); simple lateral pans/trucks
with an empty foreground can drift. Locked-off shots are the safest bet.

## 3. Reference image (how it looks)

The reference is your visual blueprint. The single highest-leverage input:

- **Show the subject and environment together.** A background-only reference
  can't tell the model how to light your subject — put the person in the
  frame, lit the way you want (e.g. your girl composited into the forest).
- Pick the most representative frame, edit it into the target look, re-upload.
- **Imperfect references are fine** — SwitchX brings only lighting/style from
  the reference and keeps your original pixels.
- Keep references consistent across shots in a sequence.

## 4. Prompting

- Be specific: name the environment, lighting, and mood —
  *"a dramatic cliff in Ireland, soft overcast lighting, highly detailed
  props"*.
- Vague prompts ("take me to heaven") produce poor results.
- The reference image is the stronger signal; the prompt steers the masked
  region.

## 5. Source footage quality

Source quality carries into the output. Feed the highest-quality source you
have; the model caps at 2,770,000 pixels and 240 frames, so downscale/trim
first (≤1080p, ≤240 frames). Recommended transcode (Resolve/ffmpeg):

```bash
ffmpeg -i input.mov -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -c:a aac -b:a 256k output.mp4
```

## 6. Iteration

Iterate cheaply at `max_resolution: 720`, and switch to `1080` for the final
render once the look locks. Alpha mattes are returned with every job — grab
them with `save_alpha: true` for compositing.