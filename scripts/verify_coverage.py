#!/usr/bin/env python3
"""Verify that the audit artifacts are arithmetically consistent and that every
evidence path in the coverage table exists.

Checks are read-only and print PASS / FAIL / WARN lines. Exits 1 if any FAIL.
Writes an optional Markdown verification report with --out.

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

REQUIRED_COVERAGE_COLUMNS = ["type", "count", "coverage_method", "evidence", "boundary"]
LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
EMPTY_EVIDENCE = {"", "-", "n/a", "na", "none", "(none)"}


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, level: str, name: str, detail: str) -> None:
        self.rows.append({"level": level, "name": name, "detail": detail})

    def ok(self, name: str, detail: str = "") -> None:
        self.add("PASS", name, detail)

    def fail(self, name: str, detail: str) -> None:
        self.add("FAIL", name, detail)

    def warn(self, name: str, detail: str) -> None:
        self.add("WARN", name, detail)

    @property
    def failed(self) -> bool:
        return any(row["level"] == "FAIL" for row in self.rows)


def resolve(path: str, root: str) -> str:
    path = os.path.expanduser(path.strip())
    if not os.path.isabs(path) and root:
        path = os.path.join(root, path)
    return os.path.normpath(path)


def load_json(checks: Checks, path: str, label: str):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # noqa: BLE001
        checks.fail(label, f"cannot read {path}: {type(exc).__name__}: {exc}")
        return None


def load_table(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        first = handle.readline()
        handle.seek(0)
        delimiter = "\t" if "\t" in first else ","
        return list(csv.DictReader(handle, delimiter=delimiter))


def check_summary(checks: Checks, summary: dict, total: int) -> None:
    counts = summary.get("counts", {})
    if counts.get("catalog_rows") == total:
        checks.ok("summary catalog_rows == catalog rows", str(total))
    else:
        checks.fail(
            "summary catalog_rows == catalog rows",
            f"summary={counts.get('catalog_rows')} catalog={total}",
        )

    by_ext = summary.get("by_ext", {})
    if sum(by_ext.values()) == total:
        checks.ok("per-extension counts sum to catalog rows", f"{len(by_ext)} extensions")
    else:
        checks.fail(
            "per-extension counts sum to catalog rows",
            f"sum={sum(by_ext.values())} catalog={total}",
        )

    by_top = summary.get("by_top", {})
    if sum(by_top.values()) == total:
        checks.ok("per-directory counts sum to catalog rows", f"{len(by_top)} top-level entries")
    else:
        checks.fail(
            "per-directory counts sum to catalog rows",
            f"sum={sum(by_top.values())} catalog={total}",
        )

    raw_seen = counts.get("raw_entries_seen")
    if raw_seen is not None:
        parts = [
            counts.get("catalog_rows", 0),
            counts.get("excluded_files_by_name", 0),
            counts.get("excluded_dirs_pruned", 0),
            counts.get("dirs_traversed", 0),
        ]
        if sum(parts) == raw_seen:
            checks.ok("catalog + excluded + traversed dirs == raw entries", str(raw_seen))
        else:
            checks.fail(
                "catalog + excluded + traversed dirs == raw entries",
                f"parts={parts} sum={sum(parts)} raw={raw_seen}",
            )

    raw_files = counts.get("raw_files_recursive")
    if raw_files is not None:
        expected = (
            counts.get("regular_files", 0)
            + counts.get("excluded_files_by_name", 0)
            + counts.get("excluded_files_nested", 0)
        )
        if expected == raw_files:
            checks.ok("recursive full-tree file count reconciles", str(raw_files))
        else:
            checks.fail(
                "recursive full-tree file count reconciles",
                f"computed={expected} reported={raw_files}",
            )

    zero = summary.get("zero_byte", [])
    if counts.get("zero_byte_files") == len(zero):
        checks.ok("zero-byte count matches list", str(len(zero)))
    else:
        checks.fail(
            "zero-byte count matches list",
            f"count={counts.get('zero_byte_files')} list={len(zero)}",
        )

    duplicates = summary.get("duplicates", [])
    redundant = sum(item.get("redundant", 0) for item in duplicates)
    if counts.get("redundant_duplicate_files") == redundant:
        checks.ok("duplicate group counts match list", f"{len(duplicates)} groups / {redundant} redundant")
    else:
        checks.fail(
            "duplicate group counts match list",
            f"reported={counts.get('redundant_duplicate_files')} computed={redundant}",
        )


def check_csv_meta(
    checks: Checks, meta_path: str, catalog_rows: list[dict], root: str, csv_exts: set[str]
) -> None:
    try:
        rows = load_table(meta_path)
    except Exception as exc:  # noqa: BLE001
        checks.fail("csv_meta readable", f"{type(exc).__name__}: {exc}")
        return
    catalog_csv = [row["path"] for row in catalog_rows if (row.get("ext") or "") in csv_exts]
    if len(rows) == len(catalog_csv):
        checks.ok("csv_meta rows == catalog tabular files", str(len(rows)))
    else:
        checks.fail(
            "csv_meta rows == catalog tabular files",
            f"meta={len(rows)} catalog={len(catalog_csv)} (limit or extension mismatch?)",
        )
    meta_paths = {row.get("path", "") for row in rows}
    missing = [path for path in catalog_csv if path not in meta_paths]
    if missing:
        checks.fail("csv_meta covers every catalog tabular file", f"missing={len(missing)} e.g. {missing[:3]}")
    else:
        checks.ok("csv_meta covers every catalog tabular file")
    errors = [row for row in rows if row.get("error")]
    if errors:
        checks.warn("csv_meta per-file errors", f"{len(errors)} file(s); inspect the error column")
    else:
        checks.ok("csv_meta has no per-file errors")

    absent = 0
    for row in rows:
        path = resolve(row.get("path", ""), root)
        if not os.path.isfile(path):
            absent += 1
    if absent:
        checks.fail("csv_meta paths resolve", f"{absent} missing on disk")
    else:
        checks.ok("csv_meta paths resolve")


def check_bundle(checks: Checks, index: dict) -> None:
    counts = index.get("counts", {})
    total = sum(
        counts.get(key, 0)
        for key in (
            "included",
            "duplicate",
            "empty",
            "unreadable",
            "too_large",
            "binary_skipped",
        )
    )
    if counts.get("selected") == total:
        checks.ok("text bundle buckets sum to selected", f"selected={total}")
    else:
        checks.fail(
            "text bundle buckets sum to selected",
            f"selected={counts.get('selected')} buckets={total}",
        )

    entries = index.get("entries", [])
    if len(entries) == counts.get("selected"):
        checks.ok("text bundle index has one entry per selected file")
    else:
        checks.fail(
            "text bundle index has one entry per selected file",
            f"entries={len(entries)} selected={counts.get('selected')}",
        )

    bad_order = 0
    last = -1
    for entry in entries:
        start = entry.get("start_line", -1)
        end = entry.get("end_line", -1)
        if start <= last or end <= start:
            bad_order += 1
        last = max(last, end)
    if bad_order:
        checks.fail("text bundle line ranges are monotonic", f"{bad_order} bad entries")
    else:
        checks.ok("text bundle line ranges are monotonic")

    bundle_path = index.get("bundle", "")
    if bundle_path and os.path.isfile(bundle_path):
        with open(bundle_path, "rb") as handle:
            lines = handle.read().count(b"\n")
        if lines >= (entries[-1]["end_line"] if entries else 0):
            checks.ok("bundle contains all indexed lines", f"bundle_lines={lines}")
        else:
            checks.fail(
                "bundle contains all indexed lines",
                f"bundle_lines={lines} last_end={entries[-1]['end_line']}",
            )
    else:
        checks.warn("bundle line check skipped", f"bundle not found: {bundle_path}")


def evidence_paths(cell: str) -> list[str]:
    parts = [part.strip() for part in cell.split(";") if part.strip()]
    cleaned = []
    for part in parts:
        if part.lower() in EMPTY_EVIDENCE:
            continue
        cleaned.append(LINE_SUFFIX.sub("", part))
    return cleaned


def check_coverage(checks: Checks, path: str, root: str) -> list[dict]:
    try:
        rows = load_table(path)
    except Exception as exc:  # noqa: BLE001
        checks.fail("coverage table readable", f"{type(exc).__name__}: {exc}")
        return []
    if not rows:
        checks.fail("coverage table has rows", "file is empty")
        return rows
    columns = {(key or "").strip().lower() for key in rows[0].keys()}
    missing = [name for name in REQUIRED_COVERAGE_COLUMNS if name not in columns]
    if missing:
        checks.fail("coverage table has required columns", f"missing={missing}")
        return rows
    checks.ok("coverage table has required columns")

    bad_counts = []
    checked = 0
    missing_paths: list[str] = []
    for row in rows:
        raw = (row.get("count") or "").strip().replace(",", "")
        try:
            if int(raw) < 0:
                bad_counts.append(raw)
        except ValueError:
            bad_counts.append(raw)
        for candidate in evidence_paths(row.get("evidence") or ""):
            checked += 1
            if not os.path.exists(resolve(candidate, root)):
                missing_paths.append(candidate)
    if bad_counts:
        checks.fail("coverage counts are integers", f"bad={bad_counts[:5]}")
    else:
        checks.ok("coverage counts are integers")
    if missing_paths:
        checks.fail(
            "coverage evidence paths exist",
            f"{len(missing_paths)} missing e.g. {missing_paths[:5]}",
        )
    else:
        checks.ok("coverage evidence paths exist", f"{checked} path(s) checked")
    return rows


def markdown(checks: Checks, coverage_rows: list[dict]) -> str:
    lines = [
        "# Coverage verification",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "| Level | Check | Detail |",
        "|---|---|---|",
    ]
    for row in checks.rows:
        detail = row["detail"].replace("|", "\\|")
        lines.append(f"| {row['level']} | {row['name']} | {detail} |")
    if coverage_rows:
        lines += ["", "## Coverage table", "", "| Type | Count | Coverage method | Evidence | Boundary |", "|---|---|---|---|---|"]
        for row in coverage_rows:
            cells = [
                (row.get(key) or "").replace("|", "\\|").replace("\n", " ")
                for key in ("type", "count", "coverage_method", "evidence", "boundary")
            ]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--csv-meta")
    parser.add_argument("--bundle-index")
    parser.add_argument("--coverage")
    parser.add_argument("--root", help="Resolve relative evidence/catalog paths")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--csv-exts", default=".csv")
    parser.add_argument("--out", help="Optional Markdown report")
    args = parser.parse_args()

    root = os.path.abspath(args.root) if args.root else ""
    checks = Checks()

    try:
        catalog_rows = load_table(args.catalog)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: cannot read catalog: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    total = len(catalog_rows)
    checks.ok("catalog readable", f"{total} rows")

    if args.expected_total is not None:
        if args.expected_total == total:
            checks.ok("catalog matches expected total", str(total))
        else:
            checks.fail(
                "catalog matches expected total",
                f"expected={args.expected_total} catalog={total}",
            )

    summary = load_json(checks, args.summary, "summary readable") if args.summary else None
    if summary:
        check_summary(checks, summary, total)

    if args.csv_meta:
        exts = {ext.strip().lower() for ext in args.csv_exts.split(",") if ext.strip()}
        check_csv_meta(checks, args.csv_meta, catalog_rows, root, exts)

    bundle = (
        load_json(checks, args.bundle_index, "bundle index readable")
        if args.bundle_index
        else None
    )
    if bundle:
        check_bundle(checks, bundle)

    coverage_rows = check_coverage(checks, args.coverage, root) if args.coverage else []

    if args.out:
        out_path = os.path.abspath(args.out)
        if root and os.path.commonpath([out_path, root]) == root:
            print(f"FAIL: refusing to write inside the corpus: {out_path}", file=sys.stderr)
            return 2
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(markdown(checks, coverage_rows))

    for row in checks.rows:
        print(f"{row['level']:<4} {row['name']}" + (f" — {row['detail']}" if row["detail"] else ""))
    failures = sum(1 for row in checks.rows if row["level"] == "FAIL")
    warnings = sum(1 for row in checks.rows if row["level"] == "WARN")
    print(f"SUMMARY pass={len(checks.rows) - failures - warnings} fail={failures} warn={warnings}")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
