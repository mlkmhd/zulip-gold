# ruff: noqa: INP001

"""Benchmark fenced Markdown conversion without starting a Zulip application."""

import argparse
import json
import platform
import statistics
import timeit
from functools import partial
from pathlib import Path
from types import ModuleType

from check_fenced_quote_parity import (
    EXTENSION,
    FENCED_RENDER_SAMPLES,
    FencedFixtureMarkdown,
    make_fenced_render_fixture,
    render_fenced_fixture,
)

EXPORT_GROUPS = {
    "dispatch": frozenset({"dispatch_fenced_input_lines"}),
    "line_callbacks": frozenset({"handle_fenced_block_line", "handle_nested_fenced_block_line"}),
}


def omit_exports(exports: frozenset[str]) -> ModuleType:
    namespace = vars(EXTENSION)
    if missing := exports - namespace.keys():
        raise RuntimeError(
            f"extension lacks exports required for this experiment: {sorted(missing)}"
        )
    fixture = ModuleType(EXTENSION.__name__)
    fixture.__dict__.update(
        (name, value) for name, value in namespace.items() if name not in exports
    )
    return fixture


def execution_order(modes: list[str], repeat: int) -> list[str]:
    offset = repeat % len(modes)
    order = modes[offset:] + modes[:offset]
    return order if repeat // len(modes) % 2 == 0 else order[::-1]


def paired_ratios(
    timings_us: dict[str, list[float]], pairs: list[tuple[str, str]]
) -> dict[str, dict[str, float | list[float]]]:
    result: dict[str, dict[str, float | list[float]]] = {}
    for numerator, denominator in pairs:
        ratios = [
            left / right
            for left, right in zip(timings_us[numerator], timings_us[denominator], strict=True)
        ]
        result[f"{numerator}/{denominator}"] = {
            "median": round(statistics.median(ratios), 5),
            "per_repeat": [round(value, 5) for value in ratios],
        }
    return result


def convert(md: FencedFixtureMarkdown, sample: str) -> str:
    return md.reset().convert(sample)


def main() -> None:
    samples = FENCED_RENDER_SAMPLES | {
        "batch": "\n\n".join([FENCED_RENDER_SAMPLES["code"], FENCED_RENDER_SAMPLES["nested"]] * 8),
        "long-code": "~~~python\n"
        + "\n".join(f"print({i}, 'message text')" for i in range(100))
        + "\n~~~",
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--samples", nargs="+", choices=list(samples))
    parser.add_argument("--highlight", choices=["off", "on", "both"], default="both")
    isolation = parser.add_mutually_exclusive_group()
    isolation.add_argument("--isolate-exports", action="store_true")
    isolation.add_argument("--isolate-parser-factory", action="store_true")
    isolation.add_argument("--compare-planner", action="store_true")
    isolation.add_argument("--compare-native-replay", action="store_true")
    parser.add_argument("--report", type=Path, help="also write paired measurements to a JSON file")
    args = parser.parse_args()
    if args.iterations < 1 or args.repeats < 1:
        parser.error("iterations and repeats must be positive")

    omissions = (
        {
            "rust_no_dispatch": EXPORT_GROUPS["dispatch"],
            "rust_no_line_callbacks": EXPORT_GROUPS["line_callbacks"],
            "rust_no_both": EXPORT_GROUPS["dispatch"] | EXPORT_GROUPS["line_callbacks"],
        }
        if args.isolate_exports
        else {}
    )
    python_parsers: set[str] = set()
    if args.isolate_parser_factory:
        without_lines = EXPORT_GROUPS["dispatch"] | EXPORT_GROUPS["line_callbacks"]
        without_factory = without_lines | {"create_fenced_block_handler"}
        omissions = {
            "rust_no_lines": without_lines,
            "rust_no_lines_or_parser": without_lines,
            "rust_no_lines_or_factory": without_factory,
            "rust_no_lines_parser_factory": without_factory,
        }
        python_parsers = {"rust_no_lines_or_parser", "rust_no_lines_parser_factory"}
    backends = {"python": None, "rust": EXTENSION} | {
        mode: omit_exports(omitted) for mode, omitted in omissions.items()
    }
    planner_modes: set[str] = set()
    scalar_planner = None
    if args.compare_planner:
        backends.update(rust_planner=None, rust_planner_and_scalar=EXTENSION)
        planner_modes.update(("rust_planner", "rust_planner_and_scalar"))
    if args.compare_native_replay:
        backends = dict(python=None, rust=None, rust_scalar_plan=None)
        planner_modes.update(("rust", "rust_scalar_plan"))
        scalar_planner = omit_exports(frozenset({"replay_markdown_block_plan"}))
    pairs = [(mode, "python") for mode in backends if mode != "python"]
    if args.isolate_exports:
        pairs.extend(
            [
                ("rust", "rust_no_dispatch"),
                ("rust", "rust_no_line_callbacks"),
                ("rust", "rust_no_both"),
                ("rust_no_dispatch", "rust_no_both"),
                ("rust_no_line_callbacks", "rust_no_both"),
            ]
        )
    if args.isolate_parser_factory:
        pairs.extend(
            [
                ("rust", "rust_no_lines"),
                ("rust_no_lines", "rust_no_lines_or_parser"),
                ("rust_no_lines", "rust_no_lines_or_factory"),
                ("rust_no_lines", "rust_no_lines_parser_factory"),
                ("rust_no_lines_or_parser", "rust_no_lines_parser_factory"),
                ("rust_no_lines_or_factory", "rust_no_lines_parser_factory"),
            ]
        )
    if args.compare_planner:
        pairs.extend(
            [
                ("rust_planner", "rust"),
                ("rust_planner_and_scalar", "rust"),
                ("rust_planner_and_scalar", "rust_planner"),
            ]
        )
    if args.compare_native_replay:
        pairs.append(("rust", "rust_scalar_plan"))
    highlighting = (False, True) if args.highlight == "both" else (args.highlight == "on",)
    modes = list(backends)
    results = []
    for highlight in highlighting:
        renderers = {
            mode: make_fenced_render_fixture(
                backend,
                default_language="javascript",
                highlight=highlight,
                python_parser=mode in python_parsers,
                block_planner=(
                    scalar_planner
                    if mode == "rust_scalar_plan"
                    else EXTENSION
                    if mode in planner_modes
                    else None
                ),
            )
            for mode, backend in backends.items()
        }
        for name in args.samples or samples:
            sample = samples[name]
            expected = render_fenced_fixture(renderers["python"], sample)
            if not isinstance(expected[0], str):
                raise RuntimeError(f"Python rendering failed for {name}, highlight={highlight}")
            for mode, md in renderers.items():
                if mode != "python" and render_fenced_fixture(md, sample) != expected:
                    raise RuntimeError(
                        f"{mode} differs from Python for {name}, highlight={highlight}"
                    )
            timers = {
                mode: timeit.Timer(partial(convert, md, sample)) for mode, md in renderers.items()
            }
            timings_us: dict[str, list[float]] = {mode: [] for mode in modes}
            iterations = (
                max(1, args.iterations // 16) if name in {"batch", "long-code"} else args.iterations
            )
            for repeat in range(args.repeats):
                for mode in execution_order(modes, repeat):
                    elapsed = timers[mode].timeit(number=iterations)
                    timings_us[mode].append(elapsed * 1e6 / iterations)
            medians = {mode: statistics.median(samples) for mode, samples in timings_us.items()}
            results.append(
                {
                    "sample": name,
                    "highlight": highlight,
                    "iterations": iterations,
                    "python_us": round(medians["python"], 2),
                    "rust_us": round(medians["rust"], 2),
                    "rust_over_python": round(medians["rust"] / medians["python"], 3),
                    "median_us": {mode: round(value, 2) for mode, value in medians.items()},
                    "paired_ratios": paired_ratios(timings_us, pairs),
                }
            )

    report = json.dumps(
        {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "extension": EXTENSION.__file__,
            "repeats": args.repeats,
            "modes": modes,
            "omitted_exports": {mode: sorted(exports) for mode, exports in omissions.items()},
            "python_parser_modes": sorted(python_parsers),
            "planner_modes": sorted(planner_modes),
            "repeat_orders": [execution_order(modes, repeat) for repeat in range(args.repeats)],
            "results": results,
        },
        indent=2,
    )
    if args.report is not None:
        args.report.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
