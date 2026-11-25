import platform
from importlib import import_module
from os import environ
from types import ModuleType


def supports_default_rust_backend() -> bool:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        return False
    try:
        distro = platform.freedesktop_os_release()
    except OSError:
        return False
    return distro.get("ID") == "ubuntu" and distro.get("VERSION_ID") == "24.04"


def load_native_contract(flag: str, contract: str) -> ModuleType | None:
    requested = environ.get(flag) == "1"
    if not requested and not supports_default_rust_backend():
        return None
    try:
        backend = import_module("_zulip_backend")
    except ImportError as error:
        if requested and (
            not isinstance(error, ModuleNotFoundError) or error.name != "_zulip_backend"
        ):
            raise
        return None
    if requested or getattr(backend, contract, None) == 1:
        return backend
    return None
