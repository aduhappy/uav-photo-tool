# -*- coding: utf-8 -*-
"""T5 smoke test helper; deliberately imports no application code."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def snapshot(root):
    return {str(p.relative_to(root)): (sha256(p), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", help="uav_photo_tool.py or v0.6 exe")
    ap.add_argument("photos")
    ap.add_argument("--confirm", default="")
    args = ap.parse_args()
    root = Path(args.photos)
    before = snapshot(root)
    wrong = subprocess.run([args.tool, "strip-gps", str(root), "--in-place", "--confirm", "WRONG"], capture_output=True)
    if wrong.returncode != 2 or snapshot(root) != before:
        print("FAIL: wrong confirmation changed files or wrong exit code", file=sys.stderr)
        return 1
    name = args.confirm or root.name
    run = subprocess.run([args.tool, "strip-gps", str(root), "--in-place", "--confirm", name], capture_output=True)
    if run.returncode not in (0, 1):
        print("FAIL: inplace command rejected: %s" % run.stderr.decode(errors="replace"), file=sys.stderr)
        return 1
    backups = sorted(root.glob("定位备份_*.zip"))
    if not backups:
        print("FAIL: backup not found", file=sys.stderr)
        return 1
    with zipfile.ZipFile(backups[-1]) as z:
        manifest = json.loads(z.read("manifest.json"))
        if any("before_sha256" not in r or "after_sha256" not in r for r in manifest["files"]):
            print("FAIL: incomplete backup manifest", file=sys.stderr)
            return 1
    restore = subprocess.run([args.tool, "restore-gps", str(root), "--backup", str(backups[-1])], capture_output=True)
    if restore.returncode != 0 or snapshot(root) != before:
        print("FAIL: restore did not reproduce original files", file=sys.stderr)
        return 1
    print("PASS: wrong confirmation untouched files; backup manifest complete; restore byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
