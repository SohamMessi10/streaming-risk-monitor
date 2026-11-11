import subprocess
import sys

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
