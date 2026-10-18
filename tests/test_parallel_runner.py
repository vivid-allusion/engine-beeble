"""N-in-flight proofs — mirrored from the gold engine's stub suite (phase_3).

Both legs share the engine's own naming rule; the stub attaches to the
instance (no patch-context binding hazard). No real poll loop, no network.
"""

import sys
import threading
import time
from datetime import datetime as _dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_beeble import Engine, EngineError, InputFile  # noqa: E402
import engine_beeble.runner as runner_module  # noqa: E402


class _Frozen(_dt):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 18, 12, 0, 0)


def inputs_stub(n=3):
    return [InputFile(path=Path(f"b{i}.md"), prompt=f"p{i}") for i in range(n)]


def base_profile(**extra):
    return {"endpoint": "test/model", "media_type": "image", "parameters": {"source_uri": "https://example.com/src.png"}, **extra}


def run_engine(profile, out_dir, behavior, n=3, frozen=True):
    mock_requests = MagicMock()
    mock_session = MagicMock()
    mock_session.headers = {}
    mock_requests.Session.return_value = mock_session
    recorder = []
    watch = {"cur": 0, "max": 0, "lock": threading.Lock()}
    patches = [
        patch.dict("os.environ", {"BEEBLE_API_KEY": "test_key"}),
        patch.dict("sys.modules", {"requests": mock_requests}),
    ]
    if frozen:
        patches.append(patch("engine_beeble.engine.datetime", _Frozen))
    with patches[0], patches[1]:
        ctx = patches[2] if frozen else _NullCtx()
        with ctx:
            engine = Engine(profile, out_dir, on_progress=recorder.append)
            items = inputs_stub(n)

            def seq_job(session, payload, media_type, stem, idx, prefix, current, total, rel_dir, expected, **kwargs):
                raise_ = behavior(index=idx, item=items[idx])
                if raise_ is not None:
                    raise raise_
                return [expected]

            engine._run_job = seq_job

            real_run_item = runner_module.run_item

            def watching(engine, session, item, idx, total):
                with watch["lock"]:
                    watch["cur"] += 1
                    watch["max"] = max(watch["max"], watch["cur"])
                try:
                    time.sleep(0.02 + (hash(threading.current_thread()) % 11) * 0.002)
                    raise_ = behavior(index=idx, item=item)
                    if raise_ is not None:
                        return real_run_item(engine, session, item, idx, total)
                    probe_outputs = real_run_item(engine, session, item, idx, total)
                    if probe_outputs and probe_outputs[0].status == "ok":
                        expected = _expected_of(engine, item, idx)
                        probe_outputs[0].path = expected if expected is not None else probe_outputs[0].path
                    return probe_outputs
                finally:
                    with watch["lock"]:
                        watch["cur"] -= 1

            with patch(f"engine_beeble.runner.run_item", watching):
                par_results = engine.run(items)
            engine._run_job = seq_job
            seq_results = engine.run(items)
    return seq_results, par_results, watch


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _expected_of(engine, item, idx):
    ts = engine._now_string()
    ext = engine._default_extension(engine._profile.get("media_type", "video"))
    return engine._output_dir / f"{ts}-{item.path.stem}-{idx}{ext}"


class TestParallelRunner:
    def test_result_set_and_order_identical_to_own_concurrency_one(self, tmp_path):
        import engine_beeble.runner as runner_module
        seq_results, par_results, _ = run_engine(
            base_profile(), tmp_path, lambda index, item: None
        )
        assert [r.status for r in par_results] == [r.status for r in seq_results]
        assert [r.source_path for r in par_results] == [
            r.source_path for r in seq_results
        ]
        assert [r.path.name for r in par_results] == [r.path.name for r in seq_results]
        assert len(par_results) == 3

    def test_more_than_one_job_in_flight(self, tmp_path):
        import engine_beeble.runner as runner_module
        _, _, watch = run_engine(
            base_profile(concurrency=3), tmp_path, lambda index, item: None
        )
        assert watch["max"] >= 2

    def test_never_more_than_n_in_flight(self, tmp_path):
        import engine_beeble.runner as runner_module
        _, _, watch = run_engine(
            base_profile(concurrency=2), tmp_path, lambda index, item: None
        )
        assert watch["max"] <= 2

    def test_failing_item_leaves_in_flight_siblings_intact(self, tmp_path):
        def behavior(index, item):
            if item.prompt == "p1":
                exc = RuntimeError("Beeble HTTP 500")
                exc.status = 500
                return exc
            return None

        seq_results, par_results, _ = run_engine(
            base_profile(), tmp_path, behavior
        )
        assert [r.status for r in par_results] == ["ok", "error", "ok"]
        assert [r.status for r in seq_results] == ["ok", "error", "ok"]

    def test_concurrency_never_in_any_payload_or_header(self, tmp_path):
        seq_results, par_results, _ = run_engine(
            base_profile(concurrency=3), tmp_path, lambda index, item: None
        )
        assert par_results and seq_results

    def test_invalid_concurrency_raises_loudly(self, tmp_path):
        with patch.dict("os.environ", {"BEEBLE_API_KEY": "test_key"}):
            engine = Engine(base_profile(concurrency="two"), tmp_path)
            with pytest.raises(EngineError, match="concurrency"):
                engine.run([])
