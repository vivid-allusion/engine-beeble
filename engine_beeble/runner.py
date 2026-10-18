"""N-in-flight execution — mirrors the gold engine's runner.

`run_batch` caps jobs in flight structurally: at most the profile's
`concurrency` futures are submitted at any moment. Per-item behaviour
reuses this engine's machinery verbatim (`_build_beeble_payload`,
`_run_job`, the source-media/empty checks) — the same poll and error
semantics as the sequential loop. Results are written by input index; a
failing item never disturbs jobs still in flight.
"""

import concurrent.futures
import threading

from .datatypes import OutputFile


class BatchState:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = False

    def is_stopped(self) -> bool:
        with self._lock:
            return self._stop

    def halt(self):
        with self._lock:
            self._stop = True

    def note_success(self):
        pass


def run_item(engine, session, item, idx, total, state) -> list[OutputFile]:
    params = dict(engine._profile.get("parameters", {}))
    stem = item.path.stem
    current = idx + 1
    prefix = f"[{current}/{total}]"
    media_type = engine._profile.get("media_type", "") or "video"
    rel_dir = str(item.metadata.get("relative_dir", "") or "")
    dest_dir = engine._output_dir / rel_dir if rel_dir else engine._output_dir
    ts = engine._now_string()
    ext = engine._default_extension(media_type)
    expected = dest_dir / f"{ts}-{stem}-{idx}{ext}"

    prompt = f"{engine._prefix}{item.prompt}{engine._suffix}".strip()
    has_reference_image = bool((item.references or {}).get("reference_image_uri"))
    if not prompt and not has_reference_image:
        return [
            OutputFile(
                source_path=item.path,
                status="error",
                error_msg="Empty prompt and no reference image provided",
                media_type=media_type,
                expected_path=expected,
            )
        ]

    if prompt:
        engine._emit(f"{prefix} 📝 Input: {item.path.name}")

    try:
        payload = engine._build_beeble_payload(params, prompt, item, media_type)
        if not payload.get(engine._source_key()):
            return [
                OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg="Missing source media (source_uri required)",
                    media_type=media_type,
                    expected_path=expected,
                )
            ]

        saved = engine._run_job(
            session,
            payload,
            media_type,
            stem,
            idx,
            prefix,
            current,
            total,
            rel_dir,
            expected,
            save_alpha=engine._save_alpha(params),
        )
        if not saved:
            return [
                OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg="No output returned from Beeble",
                    media_type=media_type,
                    expected_path=expected,
                )
            ]
        state.note_success()
        return [
            OutputFile(
                source_path=item.path,
                path=saved_path,
                status="ok",
                media_type=media_type,
                metadata={"api_payload": payload},
            )
            for saved_path in saved
        ]
    except Exception as exc:
        engine._emit(f"{prefix} Error: {exc}", level="error", current=current, total=total)
        return [
            OutputFile(
                source_path=item.path,
                status="error",
                error_msg=str(exc),
                media_type=media_type,
                expected_path=expected,
            )
        ]


def run_batch(engine, inputs) -> list[OutputFile]:
    import requests

    session = requests.Session()
    session.headers["x-api-key"] = engine._resolve_api_key()

    state = BatchState()
    results: list[list[OutputFile] | None] = [None] * len(inputs)
    inflight_cap = max(1, engine._concurrency)

    with concurrent.futures.ThreadPoolExecutor(max_workers=inflight_cap) as pool:
        pending: dict[concurrent.futures.Future, int] = {}
        next_idx = 0
        while pending or (next_idx < len(inputs) and not state.is_stopped()):
            while (
                not state.is_stopped()
                and next_idx < len(inputs)
                and len(pending) < inflight_cap
            ):
                future = pool.submit(
                    run_item, engine, session, inputs[next_idx], next_idx, len(inputs), state
                )
                pending[future] = next_idx
                next_idx += 1
            if not pending:
                break
            done, _ = concurrent.futures.wait(pending, return_when="FIRST_COMPLETED")
            for future in done:
                results[pending.pop(future)] = future.result()

    return [out for item in results if item is not None for out in item]
