#!/usr/bin/env python3
"""Synthetic audit probe only: never resolve, connect, send or execute."""
import json
import os
import sys


def main():
    denied, allowed = [], []
    events = {
        "dns": ("socket.getaddrinfo", ("example.invalid", 443, 0, 0, 0)),
        "udp": ("socket.sendto", (None, ("192.0.2.1", 443))),
        "shell": ("subprocess.Popen", ("/bin/sh", ["/bin/sh", "-c", "SYNTHETIC_NOT_EXECUTED"], None, dict(os.environ))),
        "python": ("subprocess.Popen", (sys.executable, [sys.executable, "-c", "pass"], None, dict(os.environ))),
        "exec": ("os.exec", (sys.executable, [sys.executable], dict(os.environ))),
        "env": ("subprocess.Popen", ("/usr/bin/env", ["/usr/bin/env", sys.executable], None, dict(os.environ))),
    }
    for label, (event, args) in events.items():
        try:
            sys.audit(event, *args)
        except PermissionError:
            denied.append(label)
        else:
            allowed.append(label)
    print(json.dumps({"denied": denied, "allowed": allowed, "network_attempted": False}))


if __name__ == "__main__":
    main()
