import threading
import time
import unittest
from concurrent.futures import Future


class SingleflightRegressionTests(unittest.TestCase):
    def test_completed_future_callback_must_not_deadlock_registration(self):
        """Regression for cache-hit callback executing synchronously."""
        lock = threading.RLock()
        inflight = {}
        future = Future()
        future.set_result({"cache": "aac-hit"})
        done = threading.Event()

        def exercise():
            with lock:
                inflight["key"] = future

            def clear(done_future):
                with lock:
                    if inflight.get("key") is done_future:
                        inflight.pop("key", None)

            # A completed Future invokes this callback immediately in this thread.
            future.add_done_callback(clear)
            done.set()

        thread = threading.Thread(target=exercise, daemon=True)
        thread.start()
        self.assertTrue(done.wait(1.0), "callback registration deadlocked")
        self.assertNotIn("key", inflight)


if __name__ == "__main__":
    unittest.main()
