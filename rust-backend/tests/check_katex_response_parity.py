# ruff: noqa: INP001
import argparse
import hashlib
import json
import logging
import random
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import django
import requests
from django.test import override_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare live KaTeX service response handling")
    parser.add_argument("--cases", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=962845)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mutant-strip-errors", action="store_true")
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    django.setup()
    from zerver.lib import tex

    extension = import_module("_zulip_backend")
    rng = random.Random(args.seed)
    statuses = [200, 200.0, 400, 400.0, 201, 301, 404, 500, 503, True, None, "200"]
    bodies = [b"", b" math \n", "\u2003فارسی 😀\u0085".encode(), b"\xff", b"\xc0\xaf"]
    failures = [None, None, None, requests.ReadTimeout, requests.ConnectionError, ValueError]
    checked = errors = warnings = 0
    differences: list[dict[str, object]] = []
    with override_settings(KATEX_SERVER=True, KATEX_SERVER_PORT=9755, SHARED_SECRET="test-secret"):
        for index in range(args.cases + len(statuses) * len(bodies)):
            if index < len(statuses) * len(bodies):
                status = statuses[index // len(bodies)]
                body = bodies[index % len(bodies)]
                failure = None
            else:
                status = rng.choice(statuses)
                body = rng.choice(bodies) if rng.randrange(2) else rng.randbytes(rng.randrange(150))
                failure = rng.choice(failures)
            inline = bool(rng.randrange(2))
            content = rng.choice(["x", "فارسی 😀", "", "\ud800", "\n\x00"])
            expected: object = None
            for native in [False, True]:
                response = SimpleNamespace(status_code=status, content=body)
                session = Mock()
                session.post.return_value = response
                if failure is not None:
                    session.post.side_effect = failure("controlled request failure")
                outcome: tuple[object, ...]
                with (
                    patch.object(tex, "_rust_katex_response", extension if native else None),
                    patch.object(tex, "KatexSession", return_value=session) as factory,
                    patch.object(logging, "warning") as warning,
                ):
                    try:
                        outcome = ("returned", tex.render_tex(content, inline))
                    except Exception as error:
                        outcome = ("raised", type(error).__name__, error.args)
                logs = warning.call_args_list
                if native and args.mutant_strip_errors and logs and len(logs[0].args) == 3:
                    message, code, text = logs[0].args
                    logs[0] = call(message, code, text.strip())
                actual = (outcome, factory.call_args_list, session.post.call_args_list, logs)
                if not native:
                    expected = actual
                elif actual != expected:
                    differences.append(
                        {
                            "index": index,
                            "status": status,
                            "body": repr(body),
                            "failure": None if failure is None else failure.__name__,
                            "expected": repr(expected),
                            "actual": repr(actual),
                        }
                    )
            checked += 1
            errors += outcome[0] == "raised"
            warnings += bool(logs)
            if len(differences) >= 10:
                break

    repository = Path(__file__).resolve().parents[2]
    assert extension.__file__ is not None
    sources = [
        Path(__file__).resolve(),
        repository / "rust-backend/src/python_tex.rs",
        repository / "rust-backend/src/python.rs",
        repository / "rust-backend/src/lib.rs",
        Path(tex.__file__),
        Path(extension.__file__),
        Path(sys.executable).resolve(),
    ]
    report = {
        "seed": args.seed,
        "checked": checked,
        "exceptions": errors,
        "warnings": warnings,
        "differences": differences,
        "mutant_strip_errors": args.mutant_strip_errors,
        "python": sys.version,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "source_sha256"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
