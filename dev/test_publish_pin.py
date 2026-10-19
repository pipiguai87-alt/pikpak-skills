#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for dev/publish_pin.py.

The point of the pin is that a version *name* can be moved while the bytes
cannot. These tests assert the three properties that make that true:

1. the digest is deterministic across runs,
2. any byte change in the tree is detected,
3. generated artifacts (bytecode, caches) never enter the digest, so a stray
   __pycache__ cannot make a clean tree look tampered.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN_SCRIPT = ROOT / "dev" / "publish_pin.py"
SAMPLE_SKILL = ROOT / "skills" / "pikpak-onboarding"


def load_pin_module():
    spec = importlib.util.spec_from_file_location("publish_pin", PIN_SCRIPT)
    assert spec and spec.loader, f"cannot load {PIN_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPublishPin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pin = load_pin_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.skill = Path(self.tmp.name) / "sample-skill"
        shutil.copytree(SAMPLE_SKILL, self.skill)
        # Redirect pins into the temp dir so tests never touch dev/pins.
        self.pins_tmp = Path(self.tmp.name) / "pins"
        self.pins_tmp.mkdir()
        self._prev_pin_dir = os.environ.get("PIKPAK_PIN_DIR")
        os.environ["PIKPAK_PIN_DIR"] = str(self.pins_tmp)

    def tearDown(self):
        if self._prev_pin_dir is None:
            os.environ.pop("PIKPAK_PIN_DIR", None)
        else:
            os.environ["PIKPAK_PIN_DIR"] = self._prev_pin_dir
        self.tmp.cleanup()

    def test_digest_is_deterministic(self):
        a, _ = self.pin.tree_digest(self.skill)
        b, _ = self.pin.tree_digest(self.skill)
        c, _ = self.pin.tree_digest(self.skill)
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_single_byte_change_is_detected(self):
        before, _ = self.pin.tree_digest(self.skill)
        target = self.skill / "SKILL.md"
        target.write_bytes(target.read_bytes() + b"\n# tamper\n")
        after, _ = self.pin.tree_digest(self.skill)
        self.assertNotEqual(before, after, "a one-byte change must move the digest")

    def test_generated_artifacts_do_not_enter_the_digest(self):
        clean, _ = self.pin.tree_digest(self.skill)
        cache = self.skill / "scripts" / "__pycache__"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "junk.cpython-314.pyc").write_bytes(b"\x00\x01\x02")
        (self.skill / ".DS_Store").write_bytes(b"\x00")
        after, manifest = self.pin.tree_digest(self.skill)
        self.assertEqual(clean, after, "bytecode and .DS_Store must not affect the digest")
        paths = {f["path"] for f in manifest}
        self.assertFalse(any("__pycache__" in p for p in paths))
        self.assertNotIn(".DS_Store", paths)

    def test_pin_roundtrip_and_tamper_detection(self):
        self.assertEqual(self.pin.main([str(self.skill), "--write"]), 0)
        pin_file = self.pin.pin_path_for(self.skill)
        self.assertTrue(pin_file.is_file())
        # The pin must live outside the skill tree so it never ships with the
        # skill and never changes the digest it records.
        self.assertFalse((self.skill / "publish-pin.json").exists(),
                         "the pin must not be written inside the skill directory")
        # A freshly written pin verifies.
        self.assertEqual(self.pin.main([str(self.skill), "--verify"]), 0)
        # Rewriting the pin must not move the digest.
        digest_before, _ = self.pin.tree_digest(self.skill)
        self.pin.main([str(self.skill), "--write"])
        digest_after, _ = self.pin.tree_digest(self.skill)
        self.assertEqual(digest_before, digest_after, "rewriting the pin must not move the digest")
        # Tampering is caught.
        target = self.skill / "SKILL.md"
        target.write_bytes(target.read_bytes() + b"\n# tamper\n")
        self.assertEqual(self.pin.main([str(self.skill), "--verify"]), 1)

    def test_pins_are_isolated_per_skill_name(self):
        a = Path(self.tmp.name) / "skill-a"
        b = Path(self.tmp.name) / "skill-b"
        shutil.copytree(SAMPLE_SKILL, a)
        shutil.copytree(SAMPLE_SKILL, b)
        self.assertNotEqual(self.pin.pin_path_for(a), self.pin.pin_path_for(b))
        self.pin.main([str(a), "--write"])
        # b has no pin yet, so verifying it is an input error, not a mismatch.
        self.assertEqual(self.pin.main([str(b), "--verify"]), 2)

    def test_verify_without_pin_is_an_input_error(self):
        self.assertEqual(self.pin.main([str(self.skill), "--verify"]), 2)

    def test_missing_directory_is_an_input_error(self):
        self.assertEqual(self.pin.main([str(Path(self.tmp.name) / "nope")]), 2)


if __name__ == "__main__":
    unittest.main()
