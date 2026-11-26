import re
from typing import cast

from zerver.lib.native_backend import load_native_contract

# Warning: If you change this parsing, please test using
#   zerver/tests/test_decorators.py
# And extend zerver/tests/fixtures/user_agents_unique with any new test cases
pattern = re.compile(
    r"""^ (?P<name> [^/ ]* [^0-9/(]* )
    (/ (?P<version> [^/ ]* ))?
    ([ /] .*)?
    $""",
    re.VERBOSE,
)


_reference_pattern = pattern
_user_agent_backend = load_native_contract("ZULIP_RUST_USER_AGENT_PARSE", "USER_AGENT_PARSE_API")


def parse_user_agent(user_agent: str) -> dict[str, str]:
    native = getattr(_user_agent_backend, "parse_exact_user_agent", None)
    if callable(native) and pattern is _reference_pattern:
        parsed = cast("dict[str, str] | None", native(user_agent))
        if parsed is not None:
            return parsed
    match = pattern.match(user_agent)
    assert match is not None
    return match.groupdict()
