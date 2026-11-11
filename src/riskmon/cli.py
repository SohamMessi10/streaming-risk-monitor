"""Command line: `riskmon generate`, `riskmon replay` and `riskmon dashboard`.

Exit codes: 0 success; 1 something couldn't be done (an unreadable feed, a file that can't be
written); 2 wrong command usage.
"""

from __future__ import annotations

import argparse
import json
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

    rep = commands.add_parser("replay", help="run a feed through the event-time engine and the alert rule")
    rep.add_argument("feed", type=Path, help="a JSON Lines feed, e.g. out/feed.jsonl")
    rep.add_argument("--report", type=Path, help="write the full report (JSON) here")
    rep.add_argument("--dashboard", type=Path, help="write a static HTML dashboard here")
    truth = rep.add_mutually_exclusive_group()
    truth.add_argument("--truth", type=Path, help="score alerts against this truth file (default: FEED's "
                       "<name>.truth.json, when it exists)")
    truth.add_argument("--no-truth", action="store_true", help="don't score alerts, even if a truth file exists")
    rep.add_argument("--window-ms", type=int, default=300_000, help="statistics window, event time (default 300,000)")
    rep.add_argument("--lateness-ms", type=int, default=2_000, help="allowed lateness (default 2,000)")
    rep.add_argument("--threshold", type=float, default=5.0, help="|z| that fires an alert (default 5)")
    rep.add_argument("--min-count", type=int, default=30, help="returns the window needs first (default 30)")
    rep.add_argument("--cooldown-ms", type=int, default=30_000, help="quiet time after an alert (default 30,000)")

    dash = commands.add_parser("dashboard", help="render a saved replay report as a static HTML dashboard")
    dash.add_argument("report", type=Path, help="a report written by `riskmon replay --report`")
    dash.add_argument("output", type=Path, help="the HTML file to write")
    return parser


def _write(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except OSError as exc:
        print(f"riskmon: can't write {path} ({exc.strerror})", file=sys.stderr)
        return False


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


def _load_jumps(path: Path) -> tuple[list[dict], bool | None]:
    """The injected jumps from a truth file, and whether it says the feed is synthetic."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{path} can't be read ({exc.strerror})") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{path} isn't a JSON truth file") from None
    jumps = data.get("jumps") if isinstance(data, dict) else None
    if not isinstance(jumps, list) or not all(
            isinstance(j, dict) and isinstance(j.get("symbol"), str) and isinstance(j.get("seq"), int)
            and not isinstance(j.get("seq"), bool) and isinstance(j.get("sigmas"), int | float) for j in jumps):
        raise ValueError(f"{path} has no list of jumps with symbol, seq and sigmas")
    synthetic = data.get("synthetic")
    return jumps, synthetic if isinstance(synthetic, bool) else None


def replay_command(args) -> int:
    from riskmon.alerts import AlertRule
    from riskmon.engine import Settings
    from riskmon.events import FeedError, read_feed_file
    from riskmon.generate import truth_path
    from riskmon.replay import replay

    try:
        settings = Settings(window_ms=args.window_ms, lateness_ms=args.lateness_ms)
        settings.check()
        rule = AlertRule(threshold=args.threshold, min_count=args.min_count, cooldown_ms=args.cooldown_ms)
        rule.check()
    except ValueError as exc:
        print(f"riskmon: {exc}", file=sys.stderr)
        return EXIT_USAGE
    truth_file = None if args.no_truth else args.truth
    if truth_file is None and not args.no_truth and truth_path(args.feed).is_file():
        truth_file = truth_path(args.feed)
    jumps, synthetic = None, None
    if truth_file is not None:
        try:
            jumps, synthetic = _load_jumps(truth_file)
        except ValueError as exc:
            print(f"riskmon: {exc}", file=sys.stderr)
            return EXIT_FAILED
    try:
        result = replay(read_feed_file(args.feed), settings, rule, jumps)
    except FeedError as exc:
        print(f"riskmon: {exc}", file=sys.stderr)
        return EXIT_FAILED
    report = {"feed": str(args.feed), **result.report(synthetic)}
    if args.report is not None and not _write(args.report, json.dumps(report, indent=1) + "\n"):
        return EXIT_FAILED
    if args.dashboard is not None:
        from riskmon.dashboard import render

        if not _write(args.dashboard, render(report)):
            return EXIT_FAILED
    _print_summary(args.feed, report, truth_file)
    if args.report is not None:
        print(f"report: {args.report}")
    if args.dashboard is not None:
        print(f"dashboard: {args.dashboard}")
    return EXIT_OK


def dashboard_command(args) -> int:
    from riskmon.dashboard import render

    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"riskmon: {args.report} can't be read ({exc.strerror})", file=sys.stderr)
        return EXIT_FAILED
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"riskmon: {args.report} isn't a JSON report", file=sys.stderr)
        return EXIT_FAILED
    try:
        page = render(report)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        print(f"riskmon: {args.report} isn't a report from `riskmon replay --report`", file=sys.stderr)
        return EXIT_FAILED
    if not _write(args.output, page):
        return EXIT_FAILED
    print(f"dashboard: {args.output}")
    return EXIT_OK


def _print_summary(feed: Path, report: dict, truth_file: Path | None) -> None:
    c, alerts, lag = report["counts"], report["alerts"], report["lag_ms"]
    print(f"replayed {feed}: {c['received']:,} lines, {c['released']:,} released, "
          f"{c['duplicates']:,} duplicates, {c['late']:,} late")
    print(f"alerts: {alerts['fired']:,} fired, {alerts['suppressed_by_cooldown']:,} suppressed by the cooldown")
    ev = report["evaluation"]
    if ev is not None:
        missed = ", ".join(f"{n} {reason}" for reason, n in ev["missed_by_reason"].items())
        kind = "synthetic" if report["synthetic"] else "not marked synthetic"
        print(f"against {truth_file} ({kind}): {ev['detected']} of {ev['jumps']} jumps detected"
              + (f" (missed: {missed})" if missed else "") + f", {ev['false_alerts']} false alerts")
    if lag["updates"]:
        print(f"event-time lag: median {lag['median']:,.0f} ms, p99 {lag['p99']:,} ms, max {lag['max']:,} ms")
    rate = report["timing"]["events_per_second"]
    if rate is not None:
        print(f"{rate:,} events/s on this machine")


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "replay":
        return replay_command(args)
    if args.command == "dashboard":
        return dashboard_command(args)
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
