# ruff: noqa: INP001

"""Benchmark fenced Markdown conversion without starting a Zulip application."""

import argparse
import json
import platform
import statistics
import timeit
from functools import partial

from check_fenced_quote_parity import (
    EXTENSION,
    FENCED_RENDER_SAMPLES,
    FencedFixtureMarkdown,
    make_fenced_render_fixture,
    render_fenced_fixture,
)


def convert(md: FencedFixtureMarkdown, sample: str) -> str:
    return md.reset().convert(sample)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.iterations < 1 or args.repeats < 1:
        parser.error("iterations and repeats must be positive")

    samples = FENCED_RENDER_SAMPLES | {
        "batch": "\n\n".join([FENCED_RENDER_SAMPLES["code"], FENCED_RENDER_SAMPLES["nested"]] * 8)
    }
    results = []
    for highlight in (False, True):
        renderers = {
            mode: make_fenced_render_fixture(
                backend, default_language="javascript", highlight=highlight
            )
            for mode, backend in (("python", None), ("rust", EXTENSION))
        }
        for name, sample in samples.items():
            python, rust = (render_fenced_fixture(md, sample) for md in renderers.values())
            if python != rust or isinstance(python[0], type):
                raise RuntimeError(f"unexpected rendering result for {name}, highlight={highlight}")
            timers = {
                mode: timeit.Timer(partial(convert, md, sample)) for mode, md in renderers.items()
            }
            results_by_mode: dict[str, list[float]] = {"python": [], "rust": []}
            iterations = max(1, args.iterations // 16) if name == "batch" else args.iterations
            for repeat in range(args.repeats):
                order = ("python", "rust") if repeat % 2 == 0 else ("rust", "python")
                for mode in order:
                    results_by_mode[mode].append(timers[mode].timeit(number=iterations))
            python_us, rust_us = (
                statistics.median(result) * 1e6 / iterations for result in results_by_mode.values()
            )
            results.append(
                {
                    "sample": name,
                    "highlight": highlight,
                    "iterations": iterations,
                    "python_us": round(python_us, 2),
                    "rust_us": round(rust_us, 2),
                    "rust_over_python": round(rust_us / python_us, 3),
                }
            )

    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "machine": platform.machine(),
                "extension": EXTENSION.__file__,
                "repeats": args.repeats,
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
