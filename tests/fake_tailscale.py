#!/usr/bin/env python3
"""Test-only stand-in for tailscale, installed as `tailscale` on PATH by
test_run_script.py.

`status --json` answers with this node's MagicDNS name fake-host.tail.ts.net
(CertDomains listed when FAKE_TAILSCALE_CERTS is set, null otherwise).
`serve --bg --https=P|--http=P http://127.0.0.1:P` and `serve --https=P|--http=P off`
keep a serve configuration in FAKE_TAILSCALE_STATE, which `serve status --json`
prints in tailscaled's shape. FAKE_TAILSCALE_DOWN fails every call like a stopped
tailscaled; FAKE_TAILSCALE_DENIED refuses to change the serve configuration like a
non-operator user. Nothing is proxied. Every call is logged to FAKE_TAILSCALE_LOG.
"""

import json
import os
import sys

HOST = "fake-host.tail.ts.net"


def load():
    try:
        with open(os.environ["FAKE_TAILSCALE_STATE"]) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def save(config):
    with open(os.environ["FAKE_TAILSCALE_STATE"], "w") as handle:
        json.dump(config, handle)


def status(args):
    if args != ["--json"]:
        sys.exit("fake tailscale: only status --json is supported")
    certs = [HOST] if os.environ.get("FAKE_TAILSCALE_CERTS") else None
    print(
        json.dumps(
            {
                "Version": "1.102.4-fake",
                "BackendState": "Running",
                "Self": {"HostName": "fake-host", "DNSName": HOST + ".", "Online": True},
                "MagicDNSSuffix": "tail.ts.net",
                "CertDomains": certs,
            }
        )
    )


def serve(args):
    if args[:1] == ["status"]:
        config = load()
        if "--json" in args[1:]:
            print(json.dumps(config))
        elif not config:
            print("No serve config")
        else:
            for host_port, web in config.get("Web", {}).items():
                print(host_port, "proxy", web["Handlers"]["/"]["Proxy"])
        return
    bg, scheme, port, rest = False, None, None, []
    for arg in args:
        if arg == "--bg":
            bg = True
        elif arg.startswith(("--https=", "--http=")):
            scheme, port = arg[2:].split("=")
        else:
            rest.append(arg)
    if scheme is None or len(rest) != 1:
        sys.exit(f"fake tailscale: unsupported serve arguments {args}")
    if os.environ.get("FAKE_TAILSCALE_DENIED"):
        print("sending serve config: Access denied: serve config denied", file=sys.stderr)
        print("\nUse 'sudo tailscale serve ...'.", file=sys.stderr)
        print(
            "To not require root, use 'sudo tailscale set --operator=$USER' once.", file=sys.stderr
        )
        sys.exit(1)
    config = load()
    key = f"{HOST}:{port}"
    if rest == ["off"]:
        handler = config.get("TCP", {}).get(port)
        if not handler or not handler.get(scheme.upper()):
            sys.exit(f"error: serve config does not exist for {scheme} port {port}")
        config["TCP"].pop(port)
        config.get("Web", {}).pop(key, None)
        config = {k: v for k, v in config.items() if v}
    else:
        if not bg:
            sys.exit("fake tailscale: a foreground serve would block; pass --bg")
        config.setdefault("TCP", {})[port] = {scheme.upper(): True}
        config.setdefault("Web", {})[key] = {"Handlers": {"/": {"Proxy": rest[0]}}}
    save(config)


def main():
    argv = sys.argv[1:]
    with open(os.environ["FAKE_TAILSCALE_LOG"], "a") as log:
        log.write(json.dumps(argv) + "\n")
    if os.environ.get("FAKE_TAILSCALE_DOWN"):
        print(
            "failed to connect to local tailscaled; it doesn't appear to be running",
            file=sys.stderr,
        )
        sys.exit(1)
    if argv[:1] == ["status"]:
        status(argv[1:])
    elif argv[:1] == ["serve"]:
        serve(argv[1:])
    else:
        sys.exit(f"fake tailscale: {argv[:1]} must never be called by run.sh")


if __name__ == "__main__":
    main()
