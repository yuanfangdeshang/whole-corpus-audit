#!/usr/bin/env python3
"""Bundle selected text files into one chunk-readable file with an index.

Each unique file becomes:

    FILE: <path>
    ...content...
    END: <path>

Duplicates are recorded once by MD5, empty/binary/oversized/unreadable files
are recorded with an explanatory placeholder, and the JSON index stores line
ranges plus per-bucket counts.

Standard library only. The target tree is never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

BOM = b"\xef\xbb\xbf"


def parse_exts(value: str) -> set[str]:
    exts: set[str] = set()
    for part in value.split(","):
        part = part.strip().lower()
        if part:
            exts.add(part if part.startswith(".") else "." + part)
    return exts


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="Catalog CSV from build_catalog.py")
    parser.add_argument("--out", required=True, help="Output bundle path")
    parser.add_argument("--index", required=True, help="Output index JSON path")
    parser.add_argument("--root", help="Resolve relative catalog paths against this root")
    parser.add_argument(
        "--exts",
        default=".md,.txt,.rst,.tex",
        help="Comma-separated extensions to bundle (default .md,.txt,.rst,.tex)",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=8 * 1024 * 1024, help="Skip content larger than this"
    )
    parser.add_argument(
        "--no-dedup", action="store_true", help="Include duplicate files in full"
    )
    args = parser.parse_args()

    catalog_path = os.path.abspath(args.catalog)
    if not os.path.isfile(catalog_path):
        print(f"FAIL: catalog not found: {catalog_path}", file=sys.stderr)
        return 2
    root = os.path.abspath(args.root) if args.root else ""

    out_path = os.path.abspath(args.out)
    index_path = os.path.abspath(args.index)
    if root:
        for target in (out_path, index_path):
            try:
                inside = os.path.commonpath([target, root]) == root
            except ValueError:
                inside = False
            if inside:
                print(f"FAIL: refusing to write inside the corpus: {target}", file=sys.stderr)
                return 2

    exts = parse_exts(args.exts)
    selected: list[str] = []
    with open(catalog_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ext = (row.get("ext") or "").lower()
            if ext in exts:
                selected.append(row["path"])
    selected.sort()

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)

    counts = {
        "selected": len(selected),
        "included": 0,
        "duplicate": 0,
        "empty": 0,
        "unreadable": 0,
        "too_large": 0,
        "binary_skipped": 0,
    }
    entries: list[dict] = []
    first_seen: dict[str, str] = {}
    warnings: list[str] = []
    line = 0

    with open(out_path, "w", encoding="utf-8", newline="\n") as bundle:

        def write(text: str) -> None:
            nonlocal line
            bundle.write(text)
            line += text.count("\n")

        for rel in selected:
            full = os.path.join(root, rel) if root and not os.path.isabs(rel) else rel
            status = "included"
            content = ""
            md5 = ""
            duplicate_of = ""
            size = 0
            try:
                size = os.path.getsize(full)
                if size == 0:
                    status = "empty"
                    content = "[empty file]\n"
                elif size > args.max_bytes:
                    status = "too_large"
                    content = f"[skipped: {size} bytes exceeds max {args.max_bytes}]\n"
                else:
                    with open(full, "rb") as raw:
                        data = raw.read()
                    if data.startswith(BOM):
                        data = data[len(BOM):]
                    if b"\x00" in data[:8192]:
                        status = "binary_skipped"
                        content = "[skipped: binary content (NUL byte)]\n"
                    else:
                        md5 = md5_bytes(data)
                        if not args.no_dedup and md5 in first_seen:
                            status = "duplicate"
                            duplicate_of = first_seen[md5]
                            content = f"[duplicate of {duplicate_of}; content omitted]\n"
                        else:
                            text = data.decode("utf-8", errors="replace")
                            text = text.replace("\r\n", "\n").replace("\r", "\n")
                            if text and not text.endswith("\n"):
                                text += "\n"
                            content = text
                            first_seen[md5] = rel
            except OSError as exc:
                status = "unreadable"
                content = f"[unreadable: {type(exc).__name__}: {exc}]\n"
                warnings.append(f"{rel}: {exc}")

            counts[status] += 1
            start_line = line + 1
            write(f"FILE: {rel}\n")
            content_start = line + 1
            write(content)
            content_end = line
            write(f"END: {rel}\n")
            entries.append(
                {
                    "path": rel,
                    "status": status,
                    "size": size,
                    "md5": md5,
                    "duplicate_of": duplicate_of,
                    "start_line": start_line,
                    "content_start_line": content_start,
                    "content_end_line": content_end,
                    "end_line": line,
                }
            )

    arithmetic_ok = counts["selected"] == (
        counts["included"]
        + counts["duplicate"]
        + counts["empty"]
        + counts["unreadable"]
        + counts["too_large"]
        + counts["binary_skipped"]
    )

    index = {
        "catalog": catalog_path,
        "root": root,
        "bundle": out_path,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "extensions": sorted(exts),
        "max_bytes": args.max_bytes,
        "dedup": not args.no_dedup,
        "md5_basis": "file bytes after UTF-8 BOM strip",
        "total_lines": line,
        "counts": counts,
        "entries": entries,
        "warnings": warnings[:200],
        "warnings_total": len(warnings),
    }
    with open(index_path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, ensure_ascii=False)

    print(f"OK bundle: {out_path}")
    print(f"OK index: {index_path}")
    print(
        "selected={selected} included={included} duplicate={duplicate} empty={empty} "
        "too_large={too_large} binary_skipped={binary_skipped} unreadable={unreadable}".format(
            **counts
        )
    )
    print(f"lines={line} extensions={','.join(sorted(exts))}")
    if warnings:
        print(f"WARN {len(warnings)} unreadable file(s)", file=sys.stderr)
    if not arithmetic_ok:
        print("FAIL: bucket counts do not sum to selected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
