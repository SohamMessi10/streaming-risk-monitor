"""Command line: `riskmon generate`.

Exit codes: 0 success; 1 something couldn't be done (an unreadable feed, a file that can't be
written); 2 wrong command usage.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from riskmon import __version__

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="riskmon", description="Rolling statistics and alerts over a price feed.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser("generate", help="write a synthetic feed, and a truth file of what was injected")
    gen.add_argument("output", type=Path, help="the feed to write, e.g. out/feed.jsonl")
    gen.add_argument("--symbols", default="SYNA,SYNB", help="comma-separated (default SYNA,SYNB)")
    gen.add_argument("--seed", type=int, default=1)
    gen.add_argument("--events", type=int, default=3_600, help="events per symbol (default 3,600)")
    gen.add_argument("--jump-rate", type=float, default=0.002)
    gen.add_argument("--duplicate-rate", type=float, default=0.01)
    gen.add_argument("--late-rate", type=float, default=0.002)
    return parser


def generate_command(args) -> int:
    from riskmon.generate import FeedSpec, truth_path, write

    try:
        spec = FeedSpec(symbols=tuple(s.strip() for s in args.symbols.split(",")), seed=args.seed,
                        events_per_symbol=args.events, jump_rate=args.jump_rate,
                        duplicate_rate=args.duplicate_rate, late_rate=args.late_rate)
        spec.check()
    except ValueError as exc:
        print(f"riskmon: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        truth = write(spec, args.output)
    except OSError as exc:
        print(f"riskmon: can't write {args.output} ({exc.strerror})", file=sys.stderr)
        return EXIT_FAILED
    print(f"wrote {truth.lines:,} lines ({truth.events:,} events, {len(truth.duplicates)} duplicates, "
          f"{len(truth.jumps)} jumps, {len(truth.delayed)} delayed, {len(truth.gaps)} gaps) to {args.output}")
    print(f"what was injected: {truth_path(args.output)} (synthetic data, not market prices)")
    return EXIT_OK


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return generate_command(args)


def main(argv: list[str] | None = None) -> int:
    try:
        code = run(argv)
        sys.stdout.flush()  # so a closed pipe is noticed here, not at exit
        return code
    except BrokenPipeError:
        # The reader went away (`riskmon ... | head`): stop quietly, as Python's documentation recommends.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
