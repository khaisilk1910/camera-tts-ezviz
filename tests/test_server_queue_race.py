import importlib
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import Future
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


class _FakeFlask:
    def __init__(self, *_args, **_kwargs):
        self.config = {}

    @staticmethod
    def _decorator(*_args, **_kwargs):
        def wrap(func):
            return func
        return wrap

    get = _decorator
    post = _decorator
    patch = _decorator
    errorhandler = _decorator


def _import_server_without_web_dependencies():
    flask = types.ModuleType("flask")
    flask.Flask = _FakeFlask
    flask.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
    flask.request = types.SimpleNamespace(headers={}, get_json=lambda silent=True: {})
    waitress = types.ModuleType("waitress")
    waitress.serve = lambda *_args, **_kwargs: None
    sys.modules.setdefault("flask", flask)
    sys.modules.setdefault("waitress", waitress)

    os.environ["CAMERAS_JSON"] = ""
    os.environ["DEFAULT_CAMERA"] = ""
    os.environ["API_KEY"] = "test-key"
    os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="camera-tts-test-")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


class _FakeWorker:
    def __init__(self, playback_queue, first_stop_entered, release_first_stop):
        self.queue = playback_queue
        self.action_lock = threading.Lock()
        self.stop_calls = 0
        self._stop_lock = threading.Lock()
        self.first_stop_entered = first_stop_entered
        self.release_first_stop = release_first_stop

    def stop(self, clear_queue=True):
        with self._stop_lock:
            self.stop_calls += 1
            call = self.stop_calls
        if clear_queue:
            self.queue.clear()
        if call == 1:
            self.first_stop_entered.set()
            if not self.release_first_stop.wait(timeout=2):
                raise RuntimeError("test synchronization timeout")
        return 0

    def enqueue(self, item, *, next_item=False):
        self.queue.put_nowait(item, next_item=next_item)


class ServerQueueRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _import_server_without_web_dependencies()

    def _make_worker_for_stop_test(self, status):
        server = self.server
        worker = object.__new__(server.CameraWorker)
        worker.queue = server.PlaybackQueue(2)
        worker.current_job = "job"
        worker.cancelled_jobs = set()
        worker.cancel_lock = threading.Lock()

        class Sender:
            def __init__(self):
                self.stop_calls = 0

            def stop_current(self):
                self.stop_calls += 1

        worker.sender = Sender()
        return worker

    def test_stop_during_prepare_does_not_restart_idle_sender(self):
        server = self.server
        worker = self._make_worker_for_stop_test("preparing")
        old_snapshot, old_update = server.job_snapshot, server.update_job
        try:
            server.job_snapshot = lambda _job_id: {"status": "preparing"}
            server.update_job = lambda *_args, **_kwargs: None
            self.assertEqual(worker.stop(clear_queue=True), 1)
            self.assertEqual(worker.sender.stop_calls, 0)
            self.assertIn("job", worker.cancelled_jobs)
        finally:
            server.job_snapshot, server.update_job = old_snapshot, old_update

    def test_stop_during_play_terminates_sender(self):
        server = self.server
        worker = self._make_worker_for_stop_test("playing")
        old_snapshot, old_update = server.job_snapshot, server.update_job
        try:
            server.job_snapshot = lambda _job_id: {"status": "playing"}
            server.update_job = lambda *_args, **_kwargs: None
            self.assertEqual(worker.stop(clear_queue=True), 1)
            self.assertEqual(worker.sender.stop_calls, 1)
        finally:
            server.job_snapshot, server.update_job = old_snapshot, old_update

    def test_concurrent_replace_is_atomic_per_camera(self):
        server = self.server
        first_stop_entered = threading.Event()
        release_first_stop = threading.Event()
        worker = _FakeWorker(
            server.PlaybackQueue(10), first_stop_entered, release_first_stop
        )

        old_cameras, old_workers = server.CAMERAS, server.WORKERS
        old_audio_spec = server.audio_spec
        old_audio_keys = server.audio_keys
        old_prepare = server.get_prepare_future
        old_new_job = server.new_job
        old_snapshot = server.job_snapshot
        try:
            server.CAMERAS = {"cam": {}}
            server.WORKERS = {"cam": worker}
            server.audio_spec = lambda _cfg, text, _data: {"text": text}
            server.audio_keys = lambda spec: (spec["text"], spec["text"])

            def prepare(_spec):
                future = Future()
                future.set_result({"path": "/tmp/fake.aac"})
                return future

            server.get_prepare_future = prepare
            server.new_job = lambda _camera, text: {"id": text}
            server.job_snapshot = lambda job_id: {"id": job_id}

            results = []
            errors = []

            def request(text):
                try:
                    results.append(
                        server.enqueue_request(
                            "cam", {"text": text, "queue_mode": "replace"}
                        )
                    )
                except Exception as exc:  # pragma: no cover - test failure detail
                    errors.append(exc)

            first = threading.Thread(target=request, args=("first",))
            second = threading.Thread(target=request, args=("second",))
            first.start()
            self.assertTrue(first_stop_entered.wait(timeout=1))
            second.start()
            time.sleep(0.05)
            self.assertTrue(second.is_alive(), "second REPLACE must wait for camera lock")
            release_first_stop.set()
            first.join(timeout=2)
            second.join(timeout=2)

            self.assertFalse(errors)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(worker.stop_calls, 2)
            self.assertEqual(worker.queue.qsize(), 1)
            self.assertEqual(worker.queue.get_nowait()["job_id"], "second")
        finally:
            server.CAMERAS = old_cameras
            server.WORKERS = old_workers
            server.audio_spec = old_audio_spec
            server.audio_keys = old_audio_keys
            server.get_prepare_future = old_prepare
            server.new_job = old_new_job
            server.job_snapshot = old_snapshot


if __name__ == "__main__":
    unittest.main()
