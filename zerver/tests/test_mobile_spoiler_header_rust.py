from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import native_push_text
from zerver.lib.push_notifications import get_mobile_push_content


class MobileSpoilerHeaderRustTest(SimpleTestCase):
    def test_real_preview_passes_only_header_to_native(self) -> None:
        backend = native_push_text._spoiler_header_backend
        if backend is None or not hasattr(backend, "format_mobile_spoiler_header"):
            self.skipTest("No mobile spoiler header ABI")
        template = (
            '<div class="spoiler-block"><div class="spoiler-header">{header}</div>'
            '<div class="spoiler-content">synthetic hidden body</div></div> tail'
        )
        for header in ("", "  Persian فارسی  ", "<strong>Title</strong>", "&#x3000;"):
            markup = template.format(header=header)
            with patch.object(native_push_text, "_spoiler_header_backend", None):
                expected = get_mobile_push_content(markup)
            with patch.object(
                backend, "format_mobile_spoiler_header", wraps=backend.format_mobile_spoiler_header
            ) as witness:
                actual = get_mobile_push_content(markup)
            witness.assert_called_once()
            self.assertNotIn("synthetic hidden body", witness.call_args.args[0])
            self.assertNotIn("synthetic hidden body", actual)
            self.assertEqual(actual, expected)

    def test_standalone_header_preserves_python_space_and_unicode(self) -> None:
        backend = native_push_text._spoiler_header_backend
        if backend is None or not hasattr(backend, "format_mobile_spoiler_header"):
            self.skipTest("No mobile spoiler header ABI")
        for text in ("", "  head  ", "　 \n", "\x1c" + chr(0xD800) + "\x1f"):
            trimmed = text.strip()
            expected = f"{trimmed} (…)\n" if trimmed else "(…)\n"
            self.assertEqual(backend.format_mobile_spoiler_header(text), expected)
