#!/usr/bin/env python3
"""Fingerprint every delimited text table listed in a catalog.

For each selected file: physical line count, detected delimiter, header text,
header hash, sampled ragged-row count, and a per-file error string. Tolerates
BOMs, non-UTF-8 bytes, and unreadable files.

Standard library only. The target tree is never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from datetime import datetime, timezone

BOM = b"\xef\xbb\xbf"
SAMPLE_BYTES = 64 * 1024
DELIMITER_NAMES = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}


def parse_exts(value: str) -> set[str]:
    exts: set[str] = set()
    for part in value.split(","):
        part = part.strip().lower()
        if part:
            exts.add(part if part.startswith(".") else "." + part)
    return exts


def detect_delimiter(text: str, candidates: list[str]) -> str:
    sample = "\n".join(line for line in text.splitlines()[:20] if line.strip())
    if not sample:
        return candidates[0]
    try:
        guess = csv.Sniffer().sniff(sample, delimiters="".join(candidates)).delimiter
        if guess in candidates:
            return guess
    except csv.Error:
        pass
    first = sample.splitlines()[0]
    counts = {candidate: first.count(candidate) for candidate in candidates}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else candidates[0]


def count_lines(path: str) -> int:
    lines = 0
    last = b""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    if last and last != b"\n":
        lines += 1
    return lines


def sample_header(text: str, delimiter: str) -> tuple[str, int]:
    reader = csv.reader(text.splitlines()[:1001], delimiter=delimiter)
    header: list[str] = []
    ragged = 0
    rows = 0
    try:
        header = next(reader)
        width = len(header)
        for row in reader:
            rows += 1
            if len(row) != width:
                ragged += 1
    except (csv.Error, StopIteration):
        pass
    return delimiter.join(header), ragged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="Catalog CSV from build_catalog.py")
    parser.add_argument("--out", required=True, help="Output TSV")
    parser.add_argument("--root", help="Resolve relative catalog paths against this root")
    parser.add_argument("--exts", default=".csv", help="Comma-separated extensions (default .csv)")
    parser.add_argument(
        "--delimiters", default=",;\t|", help="Candidate delimiters (default comma semicolon tab pipe)"
    )
    parser.add_argument(
        "--no-line-count", action="store_true", help="Skip physical line counting"
    )
    parser.add_argument("--limit", type=int, default=0, help="Process at most N files")
    parser.add_argument("--progress-every", type=int, default=5000)
    args = parser.parse_args()

    catalog_path = os.path.abspath(args.catalog)
    if not os.path.isfile(catalog_path):
        print(f"FAIL: catalog not found: {catalog_path}", file=sys.stderr)
        return 2
    out_path = os.path.abspath(args.out)
    if args.root:
        root = os.path.abspath(args.root)
        if os.path.commonpath([out_path, root]) == root:
            print(f"FAIL: refusing to write inside the corpus: {out_path}", file=sys.stderr)
            return 2
    root = os.path.abspath(args.root) if args.root else ""

    exts = parse_exts(args.exts)
    candidates = list(args.delimiters)
    if not candidates:
        print("FAIL: --delimiters is empty", file=sys.stderr)
        return 2

    selected: list[tuple[str, str]] = []
    with open(catalog_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row.get("ext") or "").lower() in exts:
                selected.append((row["path"], row.get("size") or "0"))
    if args.limit:
        selected = selected[: args.limit]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    done = 0
    errors = 0
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            ["path", "size", "lines", "delimiter", "header", "head_hash", "ragged_sample", "error"]
        )
        for rel, size in selected:
            full = os.path.join(root, rel) if root and not os.path.isabs(rel) else rel
            error = ""
            lines: int | str = ""
            delimiter = ","
            header = ""
            head_hash = ""
            ragged = ""
            try:
                if not os.path.isfile(full):
                    raise FileNotFoundError(full)
                with open(full, "rb") as raw:
                    sample = raw.read(SAMPLE_BYTES)
                if sample.startswith(BOM):
                    sample = sample[len(BOM):]
                text = sample.decode("utf-8", errors="replace")
                delimiter = detect_delimiter(text, candidates)
                header, ragged = sample_header(text, delimiter)
                normalized = " ".join(header.split()).lower()
                head_hash = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]
                if not args.no_line_count:
                    lines = count_lines(full)
            except Exception as exc:  # noqa: BLE001 - per-file isolation is the point
                error = f"{type(exc).__name__}: {exc}"[:300]
                errors += 1
            safe_header = header.replace("\t", " ").replace("\r", " ").replace("\n", " ")
            delimiter_name = DELIMITER_NAMES.get(delimiter, repr(delimiter))
            writer.writerow(
                [rel, size, lines, delimiter_name, safe_header, head_hash, ragged, error]
            )
            done += 1
            if args.progress_every and done % args.progress_every == 0:
                print(f"progress {done}/{len(selected)}", file=sys.stderr)

    print(f"OK csv_meta: {out_path}")
    print(
        f"files={done} extensions={','.join(sorted(exts))} errors={errors} "
        f"line_count={'off' if args.no_line_count else 'on'} "
        f"generated={datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )
    if errors:
        print(f"WARN {errors} file(s) had errors; see the error column", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
