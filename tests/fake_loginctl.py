#!/usr/bin/env python3
"""Test-only stand-in for loginctl, installed as `loginctl` on PATH by
test_run_script.py.

`enable-linger [user]` records linger in FAKE_LOGINCTL_STATE and
`show-user USER -p Linger [--value]` reports it. FAKE_LOGINCTL_DENIED refuses
enable-linger like a polkit denial. Every call is logged to FAKE_LOGINCTL_LOG.
"""

import json
import os
import sys
from pathlib import Path


def main():
    argv = sys.argv[1:]
    with open(os.environ["FAKE_LOGINCTL_LOG"], "a") as log:
        log.write(json.dumps(argv) + "\n")
    state = Path(os.environ["FAKE_LOGINCTL_STATE"])
    if argv[:1] == ["enable-linger"]:
        if os.environ.get("FAKE_LOGINCTL_DENIED"):
            print("Could not enable linger: Access denied", file=sys.stderr)
            sys.exit(1)
        state.write_text("yes")
    elif argv[:1] == ["show-user"] and "Linger" in argv:
        linger = state.read_text() if state.exists() else "no"
        print(linger if "--value" in argv else f"Linger={linger}")
    else:
        sys.exit(f"fake loginctl: unsupported arguments {argv}")


if __name__ == "__main__":
    main()
