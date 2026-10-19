#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish guard: pin a skill tree to bytes, not to a version name.

A version tag is a name someone can move — a force-push, a re-publish under the
same version, or a registry that allows overwrite. "v1.0.6, scanned three times"
can therefore mean three different inputs, and any comparison across those runs
is meaningless.

This tool resolves a skill directory to a deterministic tree digest first, so a
publish step and a later verification step can prove they are talking about the
same bytes. It is read-only: it prints a digest and a manifest, and writes
nothing unless --write is passed.

Usage:
    python3 dev/publish_pin.py skills/pikpak-onboarding
    python3 dev/publish_pin.py skills/pikpak-onboarding --write
    python3 dev/publish_pin.py --verify skills/pikpak-onboarding

Exit codes:
    0  digest computed (or matched, with --verify)
    1  --verify requested and the tree no longer matches the recorded digest
    2  bad input
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# Anything that is generated at install/publish time rather than authored is
# excluded, so a digest never changes because a cache appeared on disk.
EXCLUDE_DIRS = {"__pycache__", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd"}
EXCLUDE_NAMES = {".DS_Store"}

PIN_FILENAME = "publish-pin.json"

# Pins live outside the skill directory on purpose. A pin inside the tree would
# change the very digest it records, and would also ship to registries as part
# of the skill payload. Keeping them under dev/pins/<skill>.json leaves the
# published bytes untouched while still being committed and reviewable.
#
# The directory is overridable so tests never write into the real dev/pins.
PINS_DIR = Path(__file__).resolve().parent / "pins"


def pins_dir() -> Path:
    """Resolve the pins directory, honouring PIKPAK_PIN_DIR for tests."""
    override = os.environ.get("PIKPAK_PIN_DIR")
    return Path(override) if override else PINS_DIR


def pin_path_for(root: Path) -> Path:
    return pins_dir() / f"{root.resolve().name}.json"


def _iter_files(root: Path):
    """Yield files in a stable order, skipping generated artifacts."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if path.name in EXCLUDE_NAMES:
            continue
        # The pin file itself must not contribute to the digest it records.
        if rel.name == PIN_FILENAME and len(rel.parts) == 1:
            continue
        yield rel, path


def tree_digest(root: Path) -> tuple[str, list[dict]]:
    """Return (tree_sha256, per-file manifest) for a skill directory.

    The tree digest is over "relpath\\0size\\0filesha256\\n" lines in sorted
    order. Path separators are normalised to "/" so the digest is stable across
    platforms.
    """
    h = hashlib.sha256()
    manifest: list[dict] = []
    for rel, path in _iter_files(root):
        data = path.read_bytes()
        file_hash = hashlib.sha256(data).hexdigest()
        rel_posix = rel.as_posix()
        manifest.append({"path": rel_posix, "size": len(data), "sha256": file_hash})
        h.update(f"{rel_posix}\0{len(data)}\0{file_hash}\n".encode("utf-8"))
    return h.hexdigest(), manifest


def _skill_name(root: Path) -> str:
    return root.resolve().name


def cmd_write(root: Path) -> int:
    digest, manifest = tree_digest(root)
    pin = {
        "skill": _skill_name(root),
        "tree_sha256": digest,
        "file_count": len(manifest),
        "files": manifest,
        "note": (
            "Pinned to bytes, not to a version name. A version tag can be moved by "
            "a force-push or a re-publish; this digest cannot."
        ),
    }
    out = pin_path_for(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"tree_sha256={digest} files={len(manifest)}")
    return 0


def cmd_verify(root: Path) -> int:
    pin_path = pin_path_for(root)
    if not pin_path.is_file():
        print(f"no pin for {root} at {pin_path}", file=sys.stderr)
        return 2
    recorded = json.loads(pin_path.read_text(encoding="utf-8"))
    digest, manifest = tree_digest(root)
    if digest != recorded.get("tree_sha256"):
        print("MISMATCH", file=sys.stderr)
        print(f"  recorded={recorded.get('tree_sha256')}", file=sys.stderr)
        print(f"  actual  ={digest}", file=sys.stderr)
        rec = {f["path"]: f["sha256"] for f in recorded.get("files", [])}
        cur = {f["path"]: f["sha256"] for f in manifest}
        for p in sorted(set(rec) - set(cur)):
            print(f"  removed: {p}", file=sys.stderr)
        for p in sorted(set(cur) - set(rec)):
            print(f"  added:   {p}", file=sys.stderr)
        for p in sorted(set(rec) & set(cur)):
            if rec[p] != cur[p]:
                print(f"  changed: {p}", file=sys.stderr)
        return 1
    print(f"OK tree_sha256={digest} files={len(manifest)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="skill directory")
    parser.add_argument("--write", action="store_true", help="write publish-pin.json")
    parser.add_argument("--verify", action="store_true", help="verify against an existing pin")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    if args.write:
        return cmd_write(root)
    if args.verify:
        return cmd_verify(root)

    digest, manifest = tree_digest(root)
    if args.json:
        print(json.dumps({"skill": _skill_name(root), "tree_sha256": digest,
                          "file_count": len(manifest), "files": manifest},
                         ensure_ascii=False, indent=2))
    else:
        print(f"skill={_skill_name(root)}")
        print(f"tree_sha256={digest}")
        print(f"files={len(manifest)}")
        for f in manifest:
            print(f"  {f['sha256'][:12]}  {f['size']:>8}  {f['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
