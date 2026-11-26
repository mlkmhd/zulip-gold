import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from zerver.lib import user_agent


class UserAgentRustTest(SimpleTestCase):
    def test_native_matches_every_user_agent_fixture_entry(self) -> None:
        backend = user_agent._user_agent_backend
        if backend is None or not hasattr(backend, "parse_exact_user_agent"):
            self.skipTest("No user-agent parsing ABI")
        fixture = Path(settings.DEPLOY_ROOT) / "zerver/tests/fixtures/user_agents_unique"
        checked = 0
        for line in fixture.read_text().splitlines():
            entry = re.fullmatch(r'[0-9]+ "(.*)"', line.strip())
            assert entry is not None
            value = entry[1]
            native = backend.parse_exact_user_agent(value)
            expected = user_agent.pattern.match(value)
            assert expected is not None
            self.assertEqual(native, expected.groupdict(), value)
            checked += 1
        self.assertGreater(checked, 100)

    def test_nonstandard_input_keeps_python_parsing(self) -> None:
        backend = user_agent._user_agent_backend
        if backend is None or not hasattr(backend, "parse_exact_user_agent"):
            self.skipTest("No user-agent parsing ABI")
        for value in ("ZulipMobile/16.2.99\n", "متن/1.1", "Client", "", chr(0xD800)):
            expected = user_agent.pattern.match(value)
            assert expected is not None
            self.assertEqual(user_agent.parse_user_agent(value), expected.groupdict())
        for value in ("ZulipMobile/16.2.99\n", "Client\r", chr(0xD800)):
            self.assertIsNone(backend.parse_exact_user_agent(value))
        with patch.object(user_agent, "pattern", re.compile(r"(?P<name>.+)")):
            self.assertEqual(user_agent.parse_user_agent("Custom"), {"name": "Custom"})
