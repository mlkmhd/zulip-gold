import platform


def supports_default_rust_backend() -> bool:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        return False
    try:
        distro = platform.freedesktop_os_release()
    except OSError:
        return False
    return distro.get("ID") == "ubuntu" and distro.get("VERSION_ID") == "24.04"
