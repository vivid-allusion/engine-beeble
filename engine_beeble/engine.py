import importlib
import os
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .datatypes import EngineError, InputFile, OutputFile, ProgressEvent

_CREATE_PATH = "/switchx/generations"
_JOB_PATH = "/switchx/generations/{job_id}"


class Engine:
    PLATFORM: str = "beeble"
    PROVIDER_NAME: str = "Beeble"
    PROVIDER_HOMEPAGE: str = "https://beeble.ai"
    API_KEY_ENV_VAR: str = "BEEBLE_API_KEY"
    API_KEY_PATTERN: str = r".+"
    BASE_URL: str = "https://api.beeble.ai/v1"

    def __init__(
        self,
        profile: dict,
        output_dir: str | Path,
        api_key: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        self._profile = profile
        self._output_dir = Path(output_dir)
        self._api_key = api_key
        self._on_progress = on_progress
        self._prefix = profile.get("prompt_prefix", "")
        self._suffix = profile.get("prompt_suffix", "")
        self._base_url = (
            profile.get("base_url") or profile.get("api_base_url") or self.BASE_URL
        ).rstrip("/")
        try:
            self._poll_interval = float(profile.get("poll_interval", 2) or 2)
        except (TypeError, ValueError):
            self._poll_interval = 2.0
        try:
            self._timeout = float(profile.get("timeout_seconds") or 0)
        except (TypeError, ValueError):
            self._timeout = 0.0

    def run(self, inputs: list[InputFile]) -> list[OutputFile]:
        self._validate_preflight()

        import requests

        session = requests.Session()
        session.headers["x-api-key"] = self._resolve_api_key()

        params = dict(self._profile.get("parameters", {}))
        media_type = self._profile.get("media_type", "") or "video"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        results: list[OutputFile] = []
        total = len(inputs)

        for idx, item in enumerate(inputs):
            stem = item.path.stem
            current = idx + 1
            prefix = f"[{current}/{total}]"
            rel_dir = str(item.metadata.get("relative_dir", "") or "")
            dest_dir = self._output_dir / rel_dir if rel_dir else self._output_dir
            ts = datetime.now().strftime("%y%m%d_%H%M%S")
            ext = self._default_extension(media_type)
            expected = dest_dir / f"{ts}-{stem}-{idx}{ext}"

            prompt = f"{self._prefix}{item.prompt}{self._suffix}".strip()
            has_reference_image = bool((item.references or {}).get("reference_image_uri"))
            if not prompt and not has_reference_image:
                results.append(
                    OutputFile(
                        source_path=item.path,
                        status="error",
                        error_msg="Empty prompt and no reference image provided",
                        media_type=media_type,
                        expected_path=expected,
                    )
                )
                continue

            if prompt:
                self._emit(f"{prefix} 📝 Prompt: {prompt}")

            try:
                payload = self._build_beeble_payload(params, prompt, item, media_type)
                if not payload.get(self._source_key()):
                    results.append(
                        OutputFile(
                            source_path=item.path,
                            status="error",
                            error_msg="Missing source media (source_uri required)",
                            media_type=media_type,
                            expected_path=expected,
                        )
                    )
                    continue

                saved = self._run_job(
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
                )
                if not saved:
                    results.append(
                        OutputFile(
                            source_path=item.path,
                            status="error",
                            error_msg="No output returned from Beeble",
                            media_type=media_type,
                            expected_path=expected,
                        )
                    )
                else:
                    for saved_path in saved:
                        results.append(
                            OutputFile(
                                source_path=item.path,
                                path=saved_path,
                                status="ok",
                                media_type=media_type,
                                metadata={"api_payload": payload},
                            )
                        )
                    continue

            except Exception as exc:
                self._emit(f"{prefix} Error: {exc}", level="error", current=current, total=total)
                results.append(
                    OutputFile(
                        source_path=item.path,
                        status="error",
                        error_msg=str(exc),
                        media_type=media_type,
                        expected_path=expected,
                    )
                )

        return results

    def _build_beeble_payload(
        self, params: dict, prompt: str, item: InputFile, media_type: str
    ) -> dict:
        payload = dict(params)
        payload.pop("prompt_prefix", None)
        payload.pop("prompt_suffix", None)
        payload["generation_type"] = self._generation_type(params, media_type)
        if prompt:
            payload["prompt"] = prompt
        source_key = self._source_key()
        source = payload.get(source_key)
        if not source and item.reference_urls:
            source = item.reference_urls[0]
        if source:
            payload[source_key] = source
        for slot, urls in item.references.items():
            if not urls:
                continue
            payload[slot] = urls[0] if isinstance(urls, list) else urls
        return payload

    def _generation_type(self, params: dict, media_type: str) -> str:
        explicit = params.get("generation_type")
        if explicit in ("image", "video"):
            return explicit
        return media_type if media_type in ("image", "video") else "video"

    def _source_key(self) -> str:
        """Primary input key: profile's image_url_param, else reference_param,
        else the provider default source_uri."""
        for key in ("image_url_param", "reference_param"):
            value = self._profile.get(key)
            if value:
                return str(value)
        return "source_uri"

    def _run_job(
        self,
        session,
        payload: dict,
        media_type: str,
        stem: str,
        idx: int,
        prefix: str,
        current: int,
        total: int,
        rel_dir: str,
        expected: Path,
    ) -> list[Path]:
        response = session.post(f"{self._base_url}{_CREATE_PATH}", json=payload, timeout=60)
        if not response.ok:
            raise RuntimeError(self._error_text(response, f"Beeble HTTP {response.status_code}"))
        body = response.json()
        job_id = body.get("id")
        if not job_id:
            raise RuntimeError("No job ID in Beeble response")
        self._emit(f"{prefix} ⏳ Job created: {job_id}")

        started = time.monotonic()
        last_progress = None
        while True:
            if self._timeout and time.monotonic() - started > self._timeout:
                raise RuntimeError(f"Job {job_id} timed out after {self._timeout}s")
            job_response = session.get(
                f"{self._base_url}{_JOB_PATH.format(job_id=job_id)}", timeout=60
            )
            if not job_response.ok:
                raise RuntimeError(
                    self._error_text(job_response, f"Job poll HTTP {job_response.status_code}")
                )
            job = job_response.json()
            status = job.get("status", "")
            progress = job.get("progress")
            if progress != last_progress:
                last_progress = progress
                self._emit(f"{prefix} ⏳ Job {status} ({progress}%)")
            if status == "completed":
                self._emit(f"{prefix} ✅ Job completed")
                output = job.get("output") or {}
                render = output.get("render")
                if not render:
                    return []
                return self._download(
                    render, media_type, stem, idx, prefix, current, total, rel_dir, payload
                )
            if status == "failed":
                raise RuntimeError(f"Job failed: {job.get('error') or 'unknown error'}")
            time.sleep(self._poll_interval)

    def _download(
        self,
        url: str,
        media_type: str,
        stem: str,
        idx: int,
        prefix: str,
        current: int,
        total: int,
        rel_dir: str,
        api_payload: dict | None = None,
    ) -> list[Path]:
        dest_dir = self._output_dir / rel_dir if rel_dir else self._output_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%y%m%d_%H%M%S")
        self._emit(f"{prefix} ⬇️  Downloading...")
        with urllib.request.urlopen(url, timeout=300) as stream:
            data = stream.read()
        ext = self._sniff_extension(data, media_type)
        dest = dest_dir / f"{ts}-{stem}-{idx}{ext}"
        with open(dest, "wb") as f:
            f.write(data)
        self._emit(
            f"{prefix} 💾 Saved: {dest.name}",
            current=current,
            total=total,
            saved_path=dest,
            api_payload=api_payload,
        )
        return [dest]

    def _sniff_extension(self, data: bytes, media_type: str) -> str:
        for magic, ext in (
            (b"\xff\xd8\xff", ".jpg"),
            (b"\x89PNG\r\n\x1a\n", ".png"),
            (b"GIF87a", ".gif"),
            (b"GIF89a", ".gif"),
        ):
            if data.startswith(magic):
                return ext
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[4:8] == b"ftyp":
            return ".mp4"
        output_format = (
            str(self._profile.get("parameters", {}).get("output_format", "") or "")
            .strip()
            .lstrip(".")
            .lower()
        )
        if output_format and all(c.isalnum() for c in output_format):
            return f".{output_format}"
        return ".mp4" if media_type == "video" else ".png"

    def _default_extension(self, media_type: str) -> str:
        output_format = (
            str(self._profile.get("parameters", {}).get("output_format", "") or "")
            .strip()
            .lstrip(".")
            .lower()
        )
        if output_format and all(c.isalnum() for c in output_format):
            return f".{output_format}"
        return ".mp4" if media_type == "video" else ".png"

    def _error_text(self, response, fallback: str) -> str:
        try:
            data = response.json()
            error = data.get("error") or {}
            message = error.get("message") or data.get("message") or ""
            return message or fallback
        except ValueError:
            return fallback

    def _validate_preflight(self):
        if not self._profile.get("endpoint"):
            raise EngineError("Missing or empty 'endpoint' in profile")
        try:
            importlib.import_module("requests")
        except ImportError:
            raise EngineError("requests not installed. Run: pip install requests") from None
        if not self._resolve_api_key():
            raise EngineError(f"{self.API_KEY_ENV_VAR} not set in environment or .env file")

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        return os.environ.get(self.API_KEY_ENV_VAR, "")

    def _emit(
        self,
        message: str,
        level: str = "info",
        current: int = 0,
        total: int = 0,
        saved_path: Path | None = None,
        api_payload: dict | None = None,
    ):
        if self._on_progress:
            self._on_progress(
                ProgressEvent(
                    message=message,
                    level=level,
                    current=current,
                    total=total,
                    saved_path=saved_path,
                    api_payload=api_payload,
                )
            )