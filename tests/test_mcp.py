import subprocess
import sys
from pathlib import Path


def test_real_stdio_client_server_round_trip():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "examples/demo.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "discovered 26 tools" in result.stdout
    assert "Rejected an update from an outdated revision" in result.stdout
    assert "Demo passed" in result.stdout


def test_real_stdio_compact_workflows_record_counts_and_sizes(tmp_path):
    import json

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "measurements.json"
    result = subprocess.run(
        [sys.executable, str(root / "examples/compact_workflows.py"), "--output", str(output)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["catalog"]["tools"] == 26
    assert report["implementation_result_review"]["call_count"] == 4
    assert report["queue_informed_signoff_verdict"]["call_count"] == 3
    assert all(row["structured_bytes"] < 1200 for row in report["proof_write_sizes"])
