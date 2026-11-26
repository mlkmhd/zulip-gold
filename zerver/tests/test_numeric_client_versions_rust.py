from itertools import product
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import compatibility


class NumericClientVersionsRustTest(SimpleTestCase):
    def test_native_numeric_comparison_keeps_full_legacy_contract(self) -> None:
        backend = compatibility._numeric_version_backend
        if backend is None or not hasattr(backend, "compare_numeric_client_versions"):
            self.skipTest("No numeric client version ABI")
        versions = (
            "",
            "0",
            "1",
            "01",
            "1.2",
            "1.2.0",
            "1.002.3",
            "1.2.3",
            "1.2.10",
            "1.2.3rc",
            "1.2.3-dev",
            "0.001.2",
            "4.2\n",
            "0..1",
            "۱.۲.۳",
            "16.2.95",
            "16.2.96",
            "20.0.1",
            "9999999999999999999",
            "10000000000000000000",
        )
        for first, second in product(versions, repeat=2):
            with patch.object(compatibility, "_numeric_version_backend", None):
                expected = compatibility.version_lt(first, second)
            self.assertIs(compatibility.version_lt(first, second), expected)

        with patch.object(
            backend,
            "compare_numeric_client_versions",
            wraps=backend.compare_numeric_client_versions,
        ) as witness:
            self.assertTrue(compatibility.version_lt("16.2.95", "16.2.96"))
        witness.assert_called_once_with("16.2.95", "16.2.96")
        self.assertIsNone(backend.compare_numeric_client_versions("1.0rc", "1.0"))
