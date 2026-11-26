from itertools import product
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.worker import thumbnail


class ThumbnailFrameOptionsRustTest(SimpleTestCase):
    def test_load_options_preserve_budget_boundary_and_result_spelling(self) -> None:
        backend = thumbnail._frame_backend
        if backend is None or not hasattr(backend, "thumbnail_frame_load_options"):
            self.skipTest("No thumbnail frame options ABI")
        for frames, width, height, animated in product(
            (-1, 0, 1, 2, 5, 69), (1, 100, 200), (5, 100), (True, False)
        ):
            exact = frames * width * height
            for limit in (0, 100_000, 30_000_000.0, exact - 1, exact, exact + 1, float(exact)):
                args = (frames, width, height, animated, limit)
                with patch.object(thumbnail, "_frame_backend", None):
                    expected = thumbnail._thumbnail_load_options(*args)
                self.assertEqual(thumbnail._thumbnail_load_options(*args), expected)
        for limit in (52.5, -0.0, float("inf"), 1 << 80):
            self.assertIsNone(backend.thumbnail_frame_load_options(5, 3, 4, True, limit))
        self.assertIsNone(backend.thumbnail_frame_load_options(5, 0, 4, True, 99))
        self.assertEqual(backend.thumbnail_frame_load_options(5, 3, 4, True, 60), "n=5")
        self.assertEqual(backend.thumbnail_frame_load_options(5, 3, 4, True, 60.0), "n=5.0")
        self.assertEqual(backend.thumbnail_frame_load_options(5, 3, 4, True, 61), "n=-1")
