#!/usr/bin/env python3
"""Fail when an excluded user-owned dirty file changes from its recorded baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DEFAULT_BASELINE = Path("Docs/Evidence/OpenMassCrowd/central_crowd_excluded_files_baseline.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.project_root.resolve()
    baseline_path = args.baseline if args.baseline.is_absolute() else root / args.baseline
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    results = []
    passed = True
    for expected in baseline["files"]:
        path = root / expected["path"]
        exists = path.is_file()
        actual_size = path.stat().st_size if exists else None
        actual_hash = sha256(path) if exists else None
        unchanged = (
            exists
            and actual_size == expected["size"]
            and actual_hash == expected["sha256"]
        )
        passed = passed and unchanged
        results.append(
            {
                "path": expected["path"],
                "exists": exists,
                "expected_size": expected["size"],
                "actual_size": actual_size,
                "expected_sha256": expected["sha256"],
                "actual_sha256": actual_hash,
                "unchanged": unchanged,
            }
        )

    report = {
        "schema_version": 1,
        "baseline": str(baseline_path),
        "passed": passed,
        "checked_count": len(results),
        "files": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output if args.output.is_absolute() else root / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
