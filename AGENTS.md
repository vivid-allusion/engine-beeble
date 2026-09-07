## Architecture: Engine SDK Wrapper

This repo is an **Engine** in the studiolot ecosystem. It wraps the **Beeble**
API (SwitchX compositing) and exposes a uniform interface that Vehicles call.

### The layers

```
studiolot (TUI) → Vehicle (script) → **Beeble Engine** → Provider API
```

Vehicles like Frame Composer and Motion Conductor are SDK-agnostic. They
discover this Engine, load it via `engine_loader.py`, and call
`engine.run(inputs)`. This Engine handles all **Beeble**-specific logic.

### Contract

- **`Engine.__init__` is PURE.** No network I/O in the constructor. It only
  stores: profile dict, output_dir, api_key, on_progress callback.
- **`Engine.run(inputs: list[InputFile]) -> list[OutputFile]`** is the ONLY
  entry point Vehicles call. Returns ALL results — success and failure —
  as OutputFile objects. Never raises for per-bullet failures.
- **Error results carry `expected_path`**: the destination filename is
  precomputed BEFORE the API call (profile `output_format`/media default
  extension) so Vehicles can write error placeholders at the exact name.
- **`EngineError`** is for unrecoverable pre-flight failures only: missing
  API key, invalid profile.
- **`datatypes.py`** defines InputFile, OutputFile, ProgressEvent,
  EngineError — the gold engine-replicate interface, verbatim.
- **`metadata.py`** is zero-dependency (stdlib only). studiolot imports this
  (NOT engine.py) to read PROVIDER_NAME for the TUI label.
- **`endpoints/`** TOMLs are the model catalog. One file per model, defining
  valid parameter ranges. Generated from the provider's own docs snapshot
  (engine-beeble-docs repo) + the published OpenAPI spec — never invented.
- **`profiles/standby/`** contains publishable YAML profiles, organized by
  media category (`VID/`) mirroring `endpoints/`. When installed, these are
  seeded into Vehicle repos' `USER-FILES/02.STANDBY/`, filtered by the
  loading Vehicle's declared media type. **Video-only for now** (MC only) —
  the `switchx-image` model was removed from the catalog; reinstate from git
  history when image compositing is needed.

### Provider details

| Field | Value |
|-------|-------|
| **Platform** | `beeble` |
| **API key env var** | `BEEBLE_API_KEY` |
| **Key pattern** | any non-empty string (docs specify no prefix) |
| **Homepage** | https://beeble.ai |
| **Docs** | https://developer.beeble.ai/docs |
| **Base URL** | `https://api.beeble.ai/v1` |
| **Auth** | `x-api-key` header |
| **API style** | Async job API: `POST /v1/switchx/generations` → poll `GET /v1/switchx/generations/{job_id}` → download `output.render` |

### Engine discovery

Vehicles find this Engine via `engine_loader.py`:
- Repo directory: `engine-beeble`
- Python package: `engine_beeble`
- `engine_loader.py` handles the hyphen→underscore mapping
- Discovery order: local clone in `00_APPLICATIONS/ENGINES/` first, pip-installed package as fallback

### Media routing (SwitchX specifics)

- **Source (required):** `source_uri` — filled by the first
  `InputFile.reference_urls` entry. Profile key `image_url_param` (default
  `source_uri`) names it. Accepts `beeble://`, `https://`, or base64 data URIs.
- **Named slots:** `reference_image_uri` (style target) and `alpha_uri`
  (mask for `custom`/`select`) via `InputFile.references`.
- **Prompt optional:** allowed empty only when a reference image is present;
  otherwise surfaced as an error before the API call.
- **generation_type** from profile `parameters.generation_type`, else
  `media_type`. `alpha_mode` is required and defaults to `auto`.

### Source File Map

| File | Purpose |
|------|---------|
| `engine_beeble/engine.py` | Engine implementation — all API calls live here |
| `engine_beeble/datatypes.py` | InputFile, OutputFile, ProgressEvent, EngineError |
| `engine_beeble/metadata.py` | Zero-dependency identity constants (studiolot reads this) |
| `engine_beeble/__init__.py` | Re-exports Engine + all datatypes + `list_standby_profiles(media_type)` shelf selector |
| `engine_beeble/endpoints/` | TOML model catalog (VID) |
| `engine_beeble/profiles/standby/` | Publishable YAML profiles by category (VID) |
| `tests/test_engine.py` | Unit tests (interface + job flow, fully mocked) |
| `tests/test_endpoints.py` | TOML catalog integrity tests |
| `pyproject.toml` | Package metadata, pip-installable |
| `requirements.txt` | requests |

### Reference

Full contract: `~/Nextcloud/00-DEVELOPMENT/MISC_DEV_TOOLS/studiolot/docs/architecture/ENGINE_CONTRACT.md`
Docs snapshot: `~/Nextcloud/00-PRODUCTION/GAI_ENGINES/engine-beeble-docs/`
OpenAPI spec: `https://api.beeble.ai/developer-api-docs/openapi.json`

### Session History

- 2026-09-06 — Created engine-beeble: gold-parity interface (engine-replicate
  datatypes verbatim), async job client (POST /v1/switchx/generations → poll
  → download render), `x-api-key` auth, SwitchX media routing (source_uri +
  reference_image_uri/alpha_uri named slots, optional prompt), TOML catalog
  generated from the docs snapshot + OpenAPI spec, VID standby shelf,
  62 tests green. Live smoke test + ★ human review pending — no API key yet.
- 2026-09-06 — Video-only scope: `switchx-image` model + IMG shelf removed
  from the catalog (MC video work only); exhaustive standby YAML (all
  alpha_mode/max_resolution values + advanced options commented).