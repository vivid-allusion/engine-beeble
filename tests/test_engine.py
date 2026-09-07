import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine_beeble.metadata as meta  # noqa: E402
from engine_beeble import Engine, EngineError, InputFile, OutputFile, ProgressEvent  # noqa: E402


class _FakeStream:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _job_response(payload: dict) -> MagicMock:
    resp = MagicMock(ok=True)
    resp.json.return_value = payload
    return resp


@contextmanager
def _requests_mock():
    mock_requests = MagicMock()
    mock_session = MagicMock()
    mock_session.headers = {}
    mock_requests.Session.return_value = mock_session
    with patch.dict("sys.modules", {"requests": mock_requests}):
        yield mock_requests, mock_session


def _completed_job(render_url: str) -> dict:
    return {
        "id": "swx_1",
        "status": "completed",
        "progress": 100,
        "output": {"render": render_url, "source": "https://x/source.mp4", "alpha": "https://x/alpha.mp4"},
    }


MP4_BYTES = b"\x00\x00\x00\x18ftyp" + b"mp4data"


class TestDatatypes:
    def test_inputfile_defaults(self):
        f = InputFile(path=Path("test.md"), prompt="hello")
        assert f.path == Path("test.md")
        assert f.prompt == "hello"
        assert f.reference_urls == []
        assert f.references == {}
        assert f.metadata == {}

    def test_outputfile_defaults(self):
        o = OutputFile(source_path=Path("test.md"))
        assert o.source_path == Path("test.md")
        assert o.path is None
        assert o.status == "ok"
        assert o.error_msg == ""
        assert o.media_type == ""
        assert o.metadata == {}
        assert o.expected_path is None

    def test_outputfile_error_status(self):
        o = OutputFile(
            source_path=Path("test.md"),
            status="error",
            error_msg="timeout",
            media_type="video",
        )
        assert o.status == "error"
        assert o.error_msg == "timeout"
        assert o.media_type == "video"

    def test_progress_event_defaults(self):
        e = ProgressEvent(message="processing")
        assert e.message == "processing"
        assert e.level == "info"

    def test_engine_error_is_exception(self):
        with pytest.raises(EngineError):
            raise EngineError("test")


class TestMetadata:
    def test_provider_name(self):
        assert meta.PROVIDER_NAME == "Beeble"

    def test_platform(self):
        assert meta.PLATFORM == "beeble"

    def test_api_key_env_var(self):
        assert meta.API_KEY_ENV_VAR == "BEEBLE_API_KEY"

    def test_api_key_pattern(self):
        assert meta.API_KEY_PATTERN == r".+"

    def test_provider_homepage(self):
        assert meta.PROVIDER_HOMEPAGE == "https://beeble.ai"

    def test_metadata_matches_engine_class(self):
        assert Engine.PLATFORM == meta.PLATFORM
        assert Engine.PROVIDER_NAME == meta.PROVIDER_NAME
        assert Engine.API_KEY_ENV_VAR == meta.API_KEY_ENV_VAR
        assert Engine.API_KEY_PATTERN == meta.API_KEY_PATTERN


class TestEngineInitPurity:
    def test_init_stores_attributes(self):
        profile = {"endpoint": "switchx-video", "media_type": "video"}
        engine = Engine(profile, "/tmp/out")
        assert engine._profile == profile
        assert engine._output_dir == Path("/tmp/out")
        assert engine._api_key is None
        assert engine._on_progress is None
        assert engine._prefix == ""
        assert engine._suffix == ""
        assert engine._base_url == "https://api.beeble.ai/v1"
        assert engine._poll_interval == 2.0
        assert engine._timeout == 0.0

    def test_init_extracts_prefix_suffix(self):
        profile = {
            "endpoint": "switchx-video",
            "prompt_prefix": "Turn this into ",
            "prompt_suffix": " with cinematic lighting",
        }
        engine = Engine(profile, "/tmp/out")
        assert engine._prefix == "Turn this into "
        assert engine._suffix == " with cinematic lighting"

    def test_init_base_url_override(self):
        engine = Engine({"endpoint": "m", "base_url": "https://proxy.beeble.ai/v1/"}, "/tmp/out")
        assert engine._base_url == "https://proxy.beeble.ai/v1"

    def test_init_poll_and_timeout_from_profile(self):
        engine = Engine({"endpoint": "m", "poll_interval": 5, "timeout_seconds": "600"}, "/tmp/out")
        assert engine._poll_interval == 5.0
        assert engine._timeout == 600.0


class TestEnginePreflight:
    def test_missing_endpoint_raises(self):
        engine = Engine({"media_type": "video"}, "/tmp/out")
        with pytest.raises(EngineError, match="Missing or empty 'endpoint'"):
            engine.run([])

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_raises(self):
        with patch.dict("sys.modules", {"requests": MagicMock()}):
            engine = Engine({"endpoint": "switchx-video"}, "/tmp/out")
            with pytest.raises(EngineError, match="not set"):
                engine.run([])

    def test_requests_not_installed_raises(self):
        with patch.dict("sys.modules", {"requests": None}):
            engine = Engine({"endpoint": "switchx-video"}, "/tmp/out")
            with pytest.raises(EngineError, match="requests not installed"):
                engine.run([])


class TestEngineJobFlow:
    def _run(
        self,
        tmp_path,
        inputs,
        profile=None,
        on_progress=None,
    ):
        with _requests_mock() as (mock_requests, mock_session):
            mock_session.post.return_value = _job_response(
                {"id": "swx_1", "status": "in_queue", "progress": 0}
            )
            mock_session.get.return_value = _job_response(_completed_job("https://x/out.mp4"))
            full_profile = {
                "endpoint": "switchx-video",
                "media_type": "video",
                "image_url_param": "source_uri",
            }
            full_profile.update(profile or {})
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                with patch("time.sleep"):
                    with patch("urllib.request.urlopen", return_value=_FakeStream(MP4_BYTES)):
                        engine = Engine(full_profile, tmp_path, on_progress=on_progress)
                        results = engine.run(inputs)
                        return results, mock_session

    def test_empty_inputs(self, tmp_path):
        results, _ = self._run(tmp_path, [])
        assert results == []

    def test_run_calls_progress_callback(self, tmp_path):
        progress_calls = []
        results, _ = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
            on_progress=progress_calls.append,
        )
        assert len(progress_calls) >= 1
        assert any("Job created: swx_1" in c.message for c in progress_calls)
        assert any("Input: b.md" in c.message for c in progress_calls)

    def test_sets_api_key_header(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
        )
        assert session.headers["x-api-key"] == "test-key"

    def test_applies_prefix_suffix(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="hello", reference_urls=["https://s.example/v.mp4"])],
            profile={"prompt_prefix": "PREFIX: ", "prompt_suffix": " :SUFFIX"},
        )
        payload = session.post.call_args.kwargs["json"]
        assert payload["prompt"] == "PREFIX: hello :SUFFIX"

    def test_post_path_and_poll_path(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
        )
        assert session.post.call_args.args[0] == "https://api.beeble.ai/v1/switchx/generations"
        assert session.get.call_args.args[0] == "https://api.beeble.ai/v1/switchx/generations/swx_1"

    def test_empty_prompt_without_reference_image_is_error(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="  ", reference_urls=["https://s.example/v.mp4"])],
        )
        assert len(results) == 1
        assert results[0].status == "error"
        assert "Empty prompt" in results[0].error_msg
        session.post.assert_not_called()

    def test_empty_prompt_with_reference_image_is_allowed(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [
                InputFile(
                    path=Path("b.md"),
                    prompt="  ",
                    reference_urls=["https://s.example/v.mp4"],
                    references={"reference_image_uri": ["https://r.example/ref.png"]},
                )
            ],
        )
        assert len(results) == 1
        assert results[0].status == "ok"
        payload = session.post.call_args.kwargs["json"]
        assert "prompt" not in payload
        assert payload["reference_image_uri"] == "https://r.example/ref.png"

    def test_per_markdown_error_returns_error_outputfile(self, tmp_path):
        with _requests_mock() as (mock_requests, mock_session):
            mock_session.post.side_effect = RuntimeError("API timeout")
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                engine = Engine(full_profile, tmp_path)
                results = engine.run(
                    [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])]
                )
        assert len(results) == 1
        assert results[0].status == "error"
        assert "API timeout" in results[0].error_msg

    def test_partial_success_mixed_batch(self, tmp_path):
        with _requests_mock() as (mock_requests, mock_session):
            ok_resp = _job_response({"id": "swx_1", "status": "in_queue", "progress": 0})
            mock_session.post.side_effect = [ok_resp, RuntimeError("fail"), ok_resp]
            mock_session.get.return_value = _job_response(_completed_job("https://x/out.mp4"))
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                with patch("time.sleep"):
                    with patch("urllib.request.urlopen", return_value=_FakeStream(MP4_BYTES)):
                        engine = Engine(full_profile, tmp_path)
                        md_files = [
                            InputFile(path=Path(f"b{i}.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])
                            for i in range(3)
                        ]
                        results = engine.run(md_files)
        statuses = [r.status for r in results]
        assert statuses.count("ok") == 2
        assert statuses.count("error") == 1

    def test_completed_job_downloads_render(self, tmp_path):
        results, _ = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
        )
        assert len(results) == 1
        assert results[0].status == "ok"
        assert results[0].path is not None
        assert results[0].path.suffix == ".mp4"
        assert results[0].path.read_bytes() == MP4_BYTES

    def test_job_poll_emits_progress(self, tmp_path):
        progress_calls = []
        with _requests_mock() as (mock_requests, mock_session):
            mock_session.post.return_value = _job_response({"id": "swx_1", "status": "in_queue", "progress": 0})
            mock_session.get.side_effect = [
                _job_response({"id": "swx_1", "status": "processing", "progress": 42}),
                _job_response(_completed_job("https://x/out.mp4")),
            ]
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                with patch("time.sleep"):
                    with patch("urllib.request.urlopen", return_value=_FakeStream(MP4_BYTES)):
                        engine = Engine(full_profile, tmp_path, on_progress=progress_calls.append)
                        engine.run(
                            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])]
                        )
        messages = [c.message for c in progress_calls]
        assert any("Job created: swx_1" in m for m in messages)
        assert any("processing (42%)" in m for m in messages)
        assert any("Job completed" in m for m in messages)

    def test_failed_job_returns_error(self, tmp_path):
        with _requests_mock() as (mock_requests, mock_session):
            mock_session.post.return_value = _job_response({"id": "swx_1", "status": "in_queue"})
            mock_session.get.return_value = _job_response(
                {"id": "swx_1", "status": "failed", "error": "Source URI unreachable"}
            )
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                with patch("time.sleep"):
                    engine = Engine(full_profile, tmp_path)
                    results = engine.run(
                        [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])]
                    )
        assert results[0].status == "error"
        assert "Source URI unreachable" in results[0].error_msg

    def test_job_without_render_returns_error(self, tmp_path):
        with _requests_mock() as (mock_requests, mock_session):
            mock_session.post.return_value = _job_response({"id": "swx_1", "status": "in_queue"})
            mock_session.get.return_value = _job_response({"id": "swx_1", "status": "completed", "output": None})
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                with patch("time.sleep"):
                    engine = Engine(full_profile, tmp_path)
                    results = engine.run(
                        [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])]
                    )
        assert results[0].status == "error"
        assert "No output" in results[0].error_msg

    def test_missing_source_media_returns_error(self, tmp_path):
        results, session = self._run(tmp_path, [InputFile(path=Path("b.md"), prompt="test")])
        assert len(results) == 1
        assert results[0].status == "error"
        assert "Missing source media" in results[0].error_msg
        session.post.assert_not_called()

    def test_mirrors_relative_dir(self, tmp_path):
        results, _ = self._run(
            tmp_path,
            [
                InputFile(
                    path=Path("b.md"),
                    prompt="test",
                    reference_urls=["https://s.example/v.mp4"],
                    metadata={"relative_dir": "scene1/nested"},
                )
            ],
        )
        assert results[0].status == "ok"
        assert results[0].path.parent == tmp_path / "scene1" / "nested"

    def test_reference_urls_feed_source_uri(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
        )
        payload = session.post.call_args.kwargs["json"]
        assert payload["source_uri"] == "https://s.example/v.mp4"

    def test_named_slots_route_to_own_keys(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [
                InputFile(
                    path=Path("b.md"),
                    prompt="test",
                    reference_urls=["https://s.example/v.mp4"],
                    references={
                        "reference_image_uri": ["https://r.example/ref.png"],
                        "alpha_uri": ["https://a.example/mask.mp4"],
                    },
                )
            ],
        )
        payload = session.post.call_args.kwargs["json"]
        assert payload["source_uri"] == "https://s.example/v.mp4"
        assert payload["reference_image_uri"] == "https://r.example/ref.png"
        assert payload["alpha_uri"] == "https://a.example/mask.mp4"

    def test_generation_type_defaults_from_media_type(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
            profile={"media_type": "image"},
        )
        payload = session.post.call_args.kwargs["json"]
        assert payload["generation_type"] == "image"

    def test_generation_type_param_overrides_media_type(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
            profile={"media_type": "image", "parameters": {"generation_type": "video"}},
        )
        payload = session.post.call_args.kwargs["json"]
        assert payload["generation_type"] == "video"

    def test_profile_params_flow_into_payload(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
            profile={
                "parameters": {
                    "alpha_mode": "select",
                    "alpha_keyframe_index": 12,
                    "max_resolution": 720,
                    "seed": 42,
                }
            },
        )
        payload = session.post.call_args.kwargs["json"]
        assert payload["alpha_mode"] == "select"
        assert payload["alpha_keyframe_index"] == 12
        assert payload["max_resolution"] == 720
        assert payload["seed"] == 42

    def test_prompt_prefix_suffix_not_sent(self, tmp_path):
        results, session = self._run(
            tmp_path,
            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])],
            profile={"parameters": {"prompt_prefix": "x", "prompt_suffix": "y"}},
        )
        payload = session.post.call_args.kwargs["json"]
        assert "prompt_prefix" not in payload
        assert "prompt_suffix" not in payload

    def test_post_http_error_surfaces_message(self, tmp_path):
        with _requests_mock() as (mock_requests, mock_session):
            error_resp = MagicMock(ok=False, status_code=402)
            error_resp.json.return_value = {"error": {"code": "INSUFFICIENT_BALANCE", "message": "Balance too low"}}
            mock_session.post.return_value = error_resp
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                engine = Engine(full_profile, tmp_path)
                results = engine.run(
                    [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])]
                )
        assert results[0].status == "error"
        assert "Balance too low" in results[0].error_msg


class TestStreamSaveExtension:
    def _run_stream(self, tmp_path, data: bytes, profile: dict | None = None):
        with _requests_mock() as (mock_requests, mock_session):
            mock_session.post.return_value = _job_response({"id": "swx_1", "status": "in_queue"})
            mock_session.get.return_value = _job_response(_completed_job("https://x/out.bin"))
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            full_profile.update(profile or {})
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                with patch("time.sleep"):
                    with patch("urllib.request.urlopen", return_value=_FakeStream(data)):
                        engine = Engine(full_profile, tmp_path)
                        return engine.run(
                            [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])]
                        )

    def test_jpeg_bytes_saved_with_jpg_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\xff\xd8\xff" + b"jpegdata", profile={"media_type": "image"})
        assert results[0].status == "ok"
        assert results[0].path.suffix == ".jpg"

    def test_png_bytes_saved_with_png_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\x89PNG\r\n\x1a\n" + b"pngdata", profile={"media_type": "image"})
        assert results[0].path.suffix == ".png"

    def test_webp_bytes_saved_with_webp_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"RIFF\x00\x00\x00\x00WEBP" + b"data", profile={"media_type": "image"})
        assert results[0].path.suffix == ".webp"

    def test_mp4_bytes_saved_with_mp4_extension(self, tmp_path):
        results = self._run_stream(tmp_path, MP4_BYTES)
        assert results[0].path.suffix == ".mp4"

    def test_unknown_bytes_use_output_format_param(self, tmp_path):
        results = self._run_stream(
            tmp_path,
            b"\xde\xad\xbe\xef",
            profile={"media_type": "image", "parameters": {"output_format": "png"}},
        )
        assert results[0].path.suffix == ".png"

    def test_unknown_bytes_fall_back_to_media_type_default(self, tmp_path):
        results = self._run_stream(tmp_path, b"\xde\xad\xbe\xef")
        assert results[0].path.suffix == ".mp4"


class TestExpectedPath:
    def _run_error(self, tmp_path, profile: dict | None = None):
        with _requests_mock() as (mock_requests, mock_session):
            mock_session.post.side_effect = RuntimeError("API timeout")
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            full_profile.update(profile or {})
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                engine = Engine(full_profile, tmp_path)
                return engine.run(
                    [InputFile(path=Path("b.md"), prompt="test", reference_urls=["https://s.example/v.mp4"])]
                )

    def test_exception_error_carries_expected_path(self, tmp_path):
        results = self._run_error(tmp_path)
        assert results[0].status == "error"
        assert results[0].expected_path is not None
        assert results[0].expected_path.parent == tmp_path
        assert results[0].expected_path.name.startswith("2")
        assert results[0].expected_path.stem.endswith("-b-0")
        assert results[0].expected_path.suffix == ".mp4"

    def test_image_media_expected_path_uses_png(self, tmp_path):
        results = self._run_error(tmp_path, profile={"media_type": "image"})
        assert results[0].expected_path.suffix == ".png"

    def test_empty_prompt_error_carries_expected_path(self, tmp_path):
        with _requests_mock() as (mock_requests, mock_session):
            full_profile = {"endpoint": "switchx-video", "media_type": "video"}
            with patch.dict("os.environ", {"BEEBLE_API_KEY": "test-key"}):
                engine = Engine(full_profile, tmp_path)
                results = engine.run(
                    [InputFile(path=Path("b.md"), prompt="  ", reference_urls=["https://s.example/v.mp4"])]
                )
        assert results[0].status == "error"
        assert results[0].expected_path is not None
        assert results[0].expected_path.stem.endswith("-b-0")


class TestImports:
    def test_init_exports_all_names(self):
        from engine_beeble import (
            Engine,
            EngineError,
            InputFile,
            OutputFile,
            ProgressEvent,
        )

        assert Engine is not None
        assert InputFile is not None
        assert OutputFile is not None
        assert ProgressEvent is not None
        assert EngineError is not None

    def test_list_standby_profiles_vid_shelf(self):
        from engine_beeble import list_standby_profiles

        vids = list_standby_profiles("VID")
        assert len(vids) == 8
        assert all("VID" in p.parts for p in vids)
        names = {p.name for p in vids}
        for am in ("auto", "fill", "select", "custom"):
            for res in ("720", "1080"):
                assert f"switchx-video_{res}p_alpha_{am}.yaml" in names

    def test_list_standby_profiles_img_shelf(self):
        from engine_beeble import list_standby_profiles

        imgs = list_standby_profiles("IMG")
        assert len(imgs) == 8
        assert all("IMG" in p.parts for p in imgs)
        names = {p.name for p in imgs}
        for am in ("auto", "fill", "select", "custom"):
            for res in ("720", "1080"):
                assert f"switchx-image_{res}p_alpha_{am}.yaml" in names

    def test_no_filter_returns_every_shelf(self):
        from engine_beeble import list_standby_profiles

        all_profiles = list_standby_profiles()
        vids = list_standby_profiles("VID")
        imgs = list_standby_profiles("IMG")
        assert len(all_profiles) == len(vids) + len(imgs)

    def test_unknown_media_type_returns_empty(self):
        from engine_beeble import list_standby_profiles

        assert list_standby_profiles("TXT") == []