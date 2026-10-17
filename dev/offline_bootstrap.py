#!/usr/bin/env python3
"""Explicit -I bootstrap: install guard before an approved child entry runs."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile

DEV = Path(__file__).resolve().parent
# -I ignores PYTHONPATH, user site and CWD; -S also disables global site/.pth hooks.
# Add only this trusted repository dir before installing the child guard.
sys.path.insert(0, str(DEV))
from offline_guard import active_guard


def main():
    if len(sys.argv) != 2:
        raise SystemExit(97)
    manifest = Path(sys.argv[1])
    if not manifest.is_absolute() or manifest.is_symlink():
        raise SystemExit(97)
    launch = json.loads(manifest.read_text(encoding="utf-8"))
    env = launch["env"]
    if dict(os.environ) != env:
        changed = sorted(key for key in set(os.environ) | set(env) if os.environ.get(key) != env.get(key))
        print("offline bootstrap environment mismatch: " + ",".join(changed), file=sys.stderr)
        raise SystemExit(97)
    private = Path(launch["private"]).resolve()
    scratch = Path(launch["scratch"]).resolve()
    if not private.is_relative_to(scratch):
        raise SystemExit(97)
    script = Path(launch["script"])
    if script.is_symlink() or hashlib.sha256(script.read_bytes()).hexdigest() != launch["sha256"]:
        raise SystemExit(97)
    tempfile.tempdir = env["TMPDIR"]
    sys.argv = [str(script), *launch["args"]]
    with active_guard(private, scratch, env):
        runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
