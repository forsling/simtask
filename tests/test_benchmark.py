import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from examples.benchmark import snapshot, summarize


def test_snapshot_reads_committed_wal_without_changing_source(tmp_path):
    source = tmp_path / "source with ? and #.sqlite3"
    target = tmp_path / "copy.sqlite3"
    with closing(sqlite3.connect(source)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE preserved (value TEXT)")
        db.execute("INSERT INTO preserved VALUES ('keep this')")
        db.commit()
        before = tuple(db.iterdump())
        snapshot(source, target)
        with closing(sqlite3.connect(target)) as copied:
            assert copied.execute("SELECT value FROM preserved").fetchall() == [("keep this",)]
            copied.execute("INSERT INTO preserved VALUES ('copy only')")
            copied.commit()
        assert tuple(db.iterdump()) == before


def test_missing_source_is_not_created(tmp_path):
    source = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        snapshot(source, tmp_path / "copy.sqlite3")
    assert not source.exists()


def test_summary_retains_samples_and_sample_count():
    assert summarize({"write": [9.0, 1.0, 5.0]}) == {
        "write": {
            "n": 3,
            "median_ms": 5.0,
            "min_ms": 1.0,
            "max_ms": 9.0,
            "samples_ms": [9.0, 1.0, 5.0],
        }
    }


@pytest.mark.parametrize(
    "arguments", [["--samples", "0"], ["--samples", "1001"], ["--project", "unpaired"]]
)
def test_invalid_benchmark_options_fail_before_running(arguments):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "examples/benchmark.py"), *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
