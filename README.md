# engine-beeble

Engine wrapper for the [Beeble AI](https://beeble.ai) API — SwitchX video
and image compositing.

Part of the [studiolot](https://github.com/vivid-allusion/studiolot) Vehicle /
Engine / SDK architecture. This repo wraps the Beeble REST API behind the
uniform interface that Vehicles (Frame Composer, Motion Conductor, etc.) call.
The Vehicle never talks to Beeble directly — it calls this Engine.

See `docs/architecture/ENGINE_CONTRACT.md` in the studiolot repo for the full
interface contract.

## Quick start

```bash
git clone https://github.com/vivid-allusion/engine-beeble.git
cd engine-beeble
pip install -r requirements.txt
```

Or install via pip:

```bash
pip install engine-beeble
```

## Usage

```python
from engine_beeble import Engine, InputFile
from pathlib import Path

profile = {
    "platform": "beeble",
    "media_type": "video",
    "endpoint": "switchx-video",
    "image_url_param": "source_uri",
    "parameters": {
        "alpha_mode": "auto",
        "generation_type": "video",
        "max_resolution": 720,
    },
    "prompt_prefix": "",
    "prompt_suffix": "",
}

engine = Engine(profile=profile, output_dir="/tmp/out")

inputs = [
    InputFile(
        path=Path("bullet-001.md"),
        prompt="re-skin the scene with golden-hour lighting",
        reference_urls=["https://example.com/source.mp4"],
        references={
            "reference_image_uri": ["https://example.com/style.png"],
        },
    ),
]

results = engine.run(inputs)
for r in results:
    print(r.status, r.path)
```

## API key

Set `BEEBLE_API_KEY` in your environment or a `.env` file:

```bash
export BEEBLE_API_KEY=...
```

Get a key at: https://developer.beeble.ai/api-keys

## How the API works

Beeble is an async job API. `POST /v1/switchx/generations` creates a SwitchX
compositing job and returns a job ID (`swx_...`); the Engine polls
`GET /v1/switchx/generations/{job_id}` until `completed` (then downloads
`output.render`) or `failed` (the job error is surfaced per bullet).
Output URLs expire after 72 hours — the Engine downloads immediately.

Base URL: `https://api.beeble.ai/v1` (default). Override per profile with
`base_url`. Optional profile keys: `poll_interval` (seconds, default 2) and
`timeout_seconds` (job wait cap, 0 = no cap).

## Alpha matte output

Beeble returns three signed URLs per job: `render` (the composited output),
`source` (preprocessed source), and `alpha` (the matte). By default only
`render` is downloaded. Set `save_alpha: true` in the profile `parameters`
to also download the alpha matte as `<name>-alpha<ext>`:

```yaml
parameters:
  save_alpha: true
```

The matte (white = subject kept, black = background replaced) is the mask you
use in post to make the background transparent or re-key a scene. `save_alpha`
is engine-side only — it is never sent to the API.

## Media routing

- **Primary input (required):** `source_uri` — the source video or image.
  The first URL in `InputFile.reference_urls` fills it (profile key
  `image_url_param`, default `source_uri`). Sources can be a `beeble://` URI,
  an `https://` URL, or inline base64 data.
- **Named slots:** `reference_image_uri` (style target) and `alpha_uri`
  (mask for `custom`/`select` modes) arrive via `InputFile.references`.
- **Prompt:** optional for SwitchX when a reference image is supplied — an
  empty prompt is accepted as long as `references["reference_image_uri"]`
  is set; otherwise it is an error.
- `generation_type` is set from the profile `parameters.generation_type`
  (TOML default), falling back to the profile `media_type` (`image`/`video`).

## Endpoint models

Endpoint TOML definitions live in `endpoints/`. Each file defines a model's
valid parameter ranges — the "bounds" that the AppWizard reads to build
select menus. Beeble exposes one model (SwitchX) in two flavors:

- `switchx-video` (`VID-Models/`) — video compositing (Motion Conductor)
- `switchx-image` (`IMG-Models/`) — image compositing (Frame Composer)

## Repo structure

```
engine-beeble/
├── engine_beeble/
│   ├── __init__.py          ← re-exports Engine, InputFile, OutputFile, etc.
│   ├── engine.py            ← Engine class — all Beeble API calls live here
│   ├── datatypes.py         ← InputFile, OutputFile, ProgressEvent, EngineError
│   ├── metadata.py          ← zero-dependency constants (studiolot imports this)
│   ├── endpoints/
│   │   ├── IMG-Models/      ← switchx-image.toml
│   │   ├── VID-Models/      ← switchx-video.toml
│   │   ├── TXT-Models/      ← (reserved)
│   │   └── Vision-Models/   ← (reserved)
│   └── profiles/standby/    ← publishable standby YAMLs (IMG/VID shelves)
├── requirements.txt         ← requests>=2.28
├── .env.example             ← BEEBLE_API_KEY template
├── pyproject.toml           ← pip install engine-beeble
└── README.md
```

See `docs/architecture/ENGINE_CONTRACT.md` §6 in studiolot.