from functools import cache

from scripts.lib.zulip_tools import get_tzdata_zi
from zerver.lib.native_backend import load_native_contract

try:
    _rust_timezone_aliases = load_native_contract(
        "ZULIP_RUST_TIMEZONE_ALIASES", "TIMEZONE_ALIAS_SOURCE_API"
    )
except ImportError:
    _rust_timezone_aliases = None

_parse_timezone_alias_lines = getattr(_rust_timezone_aliases, "parse_timezone_alias_lines", None)
try:
    _rust_timezone_lookup = load_native_contract(
        "ZULIP_RUST_TIMEZONE_ALIASES", "TIMEZONE_ALIAS_LOOKUP_API"
    )
except ImportError:
    _rust_timezone_lookup = None

_lookup_timezone_alias = getattr(_rust_timezone_lookup, "lookup_timezone_alias", None)


@cache
def get_canonical_timezone_map() -> dict[str, str]:
    canonical = {}
    with get_tzdata_zi() as f:
        for line in f:
            if _parse_timezone_alias_lines is not None:
                # Parse each supplied line before reading the next one so
                # malformed input and stream errors keep their original order.
                native = _parse_timezone_alias_lines([line])
                if native is not None:
                    canonical.update(native)
                    continue
            fields = line.split()
            if fields and "link".startswith(fields[0].lower()):  # zic(8) accepts any prefix of Link
                _code, name, alias = fields
                canonical[alias] = name
    return canonical


def canonicalize_timezone(key: str) -> str:
    aliases = get_canonical_timezone_map()
    if _lookup_timezone_alias is not None:
        native = _lookup_timezone_alias(aliases, key)
        if native is not None:
            return native
    return aliases.get(key, key)
