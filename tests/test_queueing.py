import queue
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from queueing import PlaybackQueue, normalize_queue_mode  # noqa: E402


class PlaybackQueueTests(unittest.TestCase):
    def test_add_is_fifo_and_next_goes_to_front(self):
        q = PlaybackQueue(4)
        q.put_nowait({"id": "a"})
        q.put_nowait({"id": "b"})
        q.put_nowait({"id": "next"}, next_item=True)
        self.assertEqual(q.get()["id"], "next")
        self.assertEqual(q.get()["id"], "a")
        self.assertEqual(q.get()["id"], "b")

    def test_get_nowait_raises_when_empty(self):
        q = PlaybackQueue(1)
        with self.assertRaises(queue.Empty):
            q.get_nowait()

    def test_clear_returns_pending_items(self):
        q = PlaybackQueue(2)
        q.put_nowait({"id": "a"})
        q.put_nowait({"id": "b"})
        self.assertEqual([item["id"] for item in q.clear()], ["a", "b"])
        self.assertEqual(q.qsize(), 0)

    def test_queue_is_bounded(self):
        q = PlaybackQueue(1)
        q.put_nowait({"id": "a"})
        with self.assertRaises(queue.Full):
            q.put_nowait({"id": "b"})

    def test_legacy_replace_and_explicit_modes(self):
        self.assertEqual(normalize_queue_mode({}, default="replace"), "replace")
        self.assertEqual(normalize_queue_mode({"replace": False}, default="add"), "add")
        self.assertEqual(normalize_queue_mode({"replace": True}, default="add"), "replace")
        self.assertEqual(normalize_queue_mode({"queue_mode": "next"}), "next")
        with self.assertRaises(ValueError):
            normalize_queue_mode({"queue_mode": "invalid"})


if __name__ == "__main__":
    unittest.main()
