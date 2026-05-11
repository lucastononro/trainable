#!/usr/bin/env python3
"""Download license-plate detection splits from HuggingFace.

Pulls the COCO-format zips from
https://huggingface.co/datasets/keremberke/license-plate-object-detection,
extracts each split into a sibling folder, and removes the zip on success.

Usage:
    python download.py                       # valid-mini + valid + test (default)
    python download.py --split train         # add the heavy 163 MB train split
    python download.py --split valid-mini    # one split only
    python download.py --force               # re-extract even if the folder exists
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "keremberke/license-plate-object-detection"
BASE_URL = f"https://huggingface.co/datasets/{REPO}/resolve/main/data"

# Approximate sizes (bytes) for the progress meter. Server sends
# Content-Length too — these are just the fallback if the header is
# missing, so the bar still moves.
SPLIT_SIZE_HINTS = {
    "valid-mini": 72_330,
    "test": 21_917_681,
    "valid": 44_930_828,
    "train": 163_307_791,
}

DEFAULT_SPLITS = ["valid-mini", "test", "valid"]


def _download(url: str, dest: Path, label: str) -> None:
    """Streaming GET with a one-line progress meter."""
    print(f"  ↓ {label} from {url}")
    with urllib.request.urlopen(url) as resp:
        total = int(resp.headers.get("Content-Length") or SPLIT_SIZE_HINTS.get(label, 0)) or None
        chunk = 1024 * 256
        with dest.open("wb") as f:
            seen = 0
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                seen += len(buf)
                if total:
                    pct = 100.0 * seen / total
                    sys.stdout.write(
                        f"\r    {seen / 1e6:6.1f} / {total / 1e6:6.1f} MB ({pct:5.1f}%)"
                    )
                    sys.stdout.flush()
            sys.stdout.write("\n")


def fetch_split(split: str, root: Path, force: bool) -> None:
    out_dir = root / split
    if out_dir.exists() and not force:
        print(f"[skip] {split}/ already extracted (--force to re-extract)")
        return
    if out_dir.exists():
        shutil.rmtree(out_dir)

    zip_path = root / f"{split}.zip"
    try:
        _download(f"{BASE_URL}/{split}.zip", zip_path, split)
        print(f"  ⇡ extracting {split}.zip → {split}/")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(out_dir)
    finally:
        if zip_path.exists():
            zip_path.unlink()

    n_imgs = sum(1 for _ in out_dir.glob("*.jpg")) + sum(1 for _ in out_dir.glob("*.png"))
    print(f"[done] {split}/ — {n_imgs} images")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--split",
        action="append",
        choices=list(SPLIT_SIZE_HINTS.keys()),
        help="Split to download (repeat for multiple). Default: valid-mini, test, valid.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download and re-extract splits that already exist.",
    )
    args = p.parse_args()

    splits = args.split or DEFAULT_SPLITS
    root = Path(__file__).resolve().parent
    print(f"Target directory: {root}")
    print(f"Splits: {', '.join(splits)}")

    for split in splits:
        fetch_split(split, root, args.force)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
