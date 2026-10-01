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
    assert "discovered 37 tools" in result.stdout
    assert "Rejected an update from an outdated revision" in result.stdout
    assert "Demo passed" in result.stdout
