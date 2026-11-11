import json
import subprocess
import sys

import pytest

from riskmon import __version__
from riskmon.cli import main


def test_generate_writes_the_feed_and_reports_it(tmp_path, capsys):
    assert main(["generate", str(tmp_path / "feed.jsonl"), "--events", "500", "--seed", "3"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("wrote ") and "synthetic data, not market prices" in out
    assert (tmp_path / "feed.jsonl").exists() and (tmp_path / "feed.truth.json").exists()


def test_generate_refuses_a_bad_spec(tmp_path, capsys):
    assert main(["generate", str(tmp_path / "f.jsonl"), "--symbols", "A,A"]) == 2
    assert "each once" in capsys.readouterr().err
    assert not (tmp_path / "f.jsonl").exists()


def test_generate_reports_a_file_it_cannot_write(tmp_path, capsys):
    (tmp_path / "blocker").write_text("a file, not a folder")
    assert main(["generate", str(tmp_path / "blocker" / "f.jsonl"), "--events", "10"]) == 1
    assert "can't write" in capsys.readouterr().err


def test_the_installed_command_runs():
    result = subprocess.run([sys.executable, "-m", "riskmon.cli", "--version"], capture_output=True, text=True,
                            timeout=60)
    assert (result.returncode, result.stdout.strip()) == (0, f"riskmon {__version__}")


# --- replay -------------------------------------------------------------------------------------

def feed(tmp_path, events=600):
    path = tmp_path / "feed.jsonl"
    assert main(["generate", str(path), "--events", str(events), "--seed", "2", "--jump-rate", "0.01"]) == 0
    return path


def test_replay_scores_alerts_against_the_truth_file_and_writes_a_report(tmp_path, capsys):
    path = feed(tmp_path)
    capsys.readouterr()
    assert main(["replay", str(path), "--report", str(tmp_path / "out" / "report.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"replayed {path}: 1,2")
    assert f"against {tmp_path / 'feed.truth.json'} (synthetic):" in out and "jumps detected" in out
    assert "events/s on this machine" in out
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["synthetic"] is True and report["evaluation"]["jumps"] > 0
    assert report["settings"] == {"window_ms": 300_000, "lateness_ms": 2_000, "rebuild_every": 1_000,
                                  "threshold": 5.0, "min_count": 30, "cooldown_ms": 30_000}


def test_replay_passes_its_settings_through(tmp_path, capsys):
    path = feed(tmp_path)
    report_path = tmp_path / "r.json"
    assert main(["replay", str(path), "--report", str(report_path), "--window-ms", "60000", "--lateness-ms", "0",
                 "--threshold", "4.5", "--min-count", "10", "--cooldown-ms", "0"]) == 0
    assert json.loads(report_path.read_text())["settings"] == {
        "window_ms": 60_000, "lateness_ms": 0, "rebuild_every": 1_000, "threshold": 4.5, "min_count": 10,
        "cooldown_ms": 0}


def test_replay_without_a_truth_file(tmp_path, capsys):
    path = feed(tmp_path)
    capsys.readouterr()
    assert main(["replay", str(path), "--no-truth"]) == 0
    assert "jumps detected" not in capsys.readouterr().out
    (tmp_path / "feed.truth.json").unlink()
    assert main(["replay", str(path)]) == 0
    assert "jumps detected" not in capsys.readouterr().out


def test_replay_with_a_named_truth_file(tmp_path, capsys):
    path = feed(tmp_path)
    moved = tmp_path / "elsewhere.json"
    (tmp_path / "feed.truth.json").rename(moved)
    capsys.readouterr()
    assert main(["replay", str(path), "--truth", str(moved)]) == 0
    assert f"against {moved} (synthetic)" in capsys.readouterr().out


def test_replay_says_when_a_truth_file_is_not_marked_synthetic(tmp_path, capsys):
    path = feed(tmp_path)
    truth = tmp_path / "feed.truth.json"
    truth.write_text(json.dumps({**json.loads(truth.read_text()), "synthetic": False}))
    capsys.readouterr()
    assert main(["replay", str(path), "--report", str(tmp_path / "r.json")]) == 0
    assert "(not marked synthetic)" in capsys.readouterr().out
    assert json.loads((tmp_path / "r.json").read_text())["synthetic"] is False


@pytest.mark.parametrize("args, message", [
    (["--threshold", "0"], "threshold must be a positive number"),
    (["--min-count", "1"], "min count must be"),
    (["--cooldown-ms", "-1"], "cooldown must be"),
    (["--window-ms", "0"], "window ms must be"),
    (["--lateness-ms", "-5"], "lateness ms must be"),
])
def test_replay_refuses_bad_settings(tmp_path, capsys, args, message):
    path = feed(tmp_path)
    assert main(["replay", str(path), *args]) == 2
    assert message in capsys.readouterr().err


def test_replay_reports_a_bad_feed_with_its_line(tmp_path, capsys):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"symbol": "A", "time": 1, "seq": 0, "price": 1.0}\n{"symbol": "A", "time": 2}\n')
    assert main(["replay", str(path)]) == 1
    assert "line 2" in capsys.readouterr().err
    assert main(["replay", str(tmp_path / "missing.jsonl")]) == 1
    assert "can't be read" in capsys.readouterr().err


@pytest.mark.parametrize("content, message", [
    ("not json", "isn't a JSON truth file"),
    ('{"jumps": "none"}', "no list of jumps"),
    ('{"jumps": [{"symbol": "A", "seq": true, "sigmas": 8}]}', "no list of jumps"),
    ('[1, 2]', "no list of jumps"),
])
def test_replay_reports_a_bad_truth_file(tmp_path, capsys, content, message):
    path = feed(tmp_path)
    (tmp_path / "feed.truth.json").write_text(content)
    assert main(["replay", str(path)]) == 1
    assert message in capsys.readouterr().err


def test_replay_reports_a_report_it_cannot_write(tmp_path, capsys):
    path = feed(tmp_path)
    (tmp_path / "blocker").write_text("a file, not a folder")
    assert main(["replay", str(path), "--report", str(tmp_path / "blocker" / "r.json")]) == 1
    assert "can't write" in capsys.readouterr().err


# --- dashboard ----------------------------------------------------------------------------------

def test_replay_writes_a_dashboard(tmp_path, capsys):
    path = feed(tmp_path)
    capsys.readouterr()
    assert main(["replay", str(path), "--dashboard", str(tmp_path / "site" / "d.html")]) == 0
    assert f"dashboard: {tmp_path / 'site' / 'd.html'}" in capsys.readouterr().out
    page = (tmp_path / "site" / "d.html").read_text()
    assert page.startswith("<!doctype html>") and f"Replay of {path}" in page


def test_a_saved_report_renders_the_same_dashboard(tmp_path, capsys):
    path = feed(tmp_path)
    assert main(["replay", str(path), "--report", str(tmp_path / "r.json"), "--dashboard", str(tmp_path / "a.html")]) == 0
    capsys.readouterr()
    assert main(["dashboard", str(tmp_path / "r.json"), str(tmp_path / "b.html")]) == 0
    assert capsys.readouterr().out == f"dashboard: {tmp_path / 'b.html'}\n"
    assert (tmp_path / "a.html").read_text() == (tmp_path / "b.html").read_text()


@pytest.mark.parametrize("content, message", [
    (None, "can't be read"),
    ("{not json", "isn't a JSON report"),
    ('{"counts": {}}', "isn't a report from `riskmon replay --report`"),
    ("[1, 2, 3]", "isn't a report from `riskmon replay --report`"),
])
def test_dashboard_refuses_what_is_not_a_report(tmp_path, capsys, content, message):
    report = tmp_path / "r.json"
    if content is not None:
        report.write_text(content)
    assert main(["dashboard", str(report), str(tmp_path / "d.html")]) == 1
    assert message in capsys.readouterr().err
    assert not (tmp_path / "d.html").exists()


def test_dashboards_report_a_file_they_cannot_write(tmp_path, capsys):
    path = feed(tmp_path)
    (tmp_path / "blocker").write_text("a file, not a folder")
    assert main(["replay", str(path), "--dashboard", str(tmp_path / "blocker" / "d.html")]) == 1
    assert "can't write" in capsys.readouterr().err
    assert main(["replay", str(path), "--report", str(tmp_path / "r.json")]) == 0
    assert main(["dashboard", str(tmp_path / "r.json"), str(tmp_path / "blocker" / "d.html")]) == 1
    assert "can't write" in capsys.readouterr().err
