#!/usr/bin/env python3
"""Build a read-only file catalog for a large corpus.

Writes one CSV row per included file and a JSON summary that records the
counting basis (what was excluded and why), per-extension and per-directory
totals, zero-byte files, and MD5 duplicate groups.

Standard library only. The target tree is never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

DEFAULT_EXCLUDE_DIRS = (
    ".git,.hg,.svn,.deps,.venv,venv,env,node_modules,__pycache__,"
    ".mypy_cache,.pytest_cache,.ipynb_checkpoints,.tox,.ruff_cache,"
    ".cache,.Trash"
)
DEFAULT_EXCLUDE_NAMES = ".DS_Store,Thumbs.db,desktop.ini"
HASH_CHUNK = 1024 * 1024


def split_csv_arg(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_exts(value: str) -> set[str]:
    exts: set[str] = set()
    for part in split_csv_arg(value):
        exts.add(part.lower() if part.startswith(".") else "." + part.lower())
    return exts


def extension_of(name: str) -> str:
    base = name.lower()
    if base.startswith(".") and base.count(".") == 1:
        return "<none>"
    ext = os.path.splitext(base)[1]
    return ext if ext else "<none>"


def md5_of(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def birth_info(stat_result: os.stat_result) -> tuple[str, str]:
    value = getattr(stat_result, "st_birthtime", None)
    if value:
        return (
            datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds"),
            "birthtime",
        )
    return "", "unavailable"


def fmt_time(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")


def is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), root]) == root
    except ValueError:
        return False


def count_tree(root: str) -> tuple[int, int]:
    """Count every file and byte under a directory without following links."""
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            files += 1
            if not os.path.islink(full):
                total += st.st_size
    return files, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Corpus root (read-only)")
    parser.add_argument("--out", required=True, help="Output catalog CSV")
    parser.add_argument("--summary", help="Output summary JSON")
    parser.add_argument(
        "--exclude-dirs",
        default=DEFAULT_EXCLUDE_DIRS,
        help="Comma-separated directory names to prune (default: vendor/VCS/caches)",
    )
    parser.add_argument(
        "--exclude-names",
        default=DEFAULT_EXCLUDE_NAMES,
        help="Comma-separated file names to skip",
    )
    parser.add_argument(
        "--hash-exts",
        default="",
        help="Comma-separated extensions to MD5 (e.g. .csv,.json)",
    )
    parser.add_argument(
        "--hash-max-bytes",
        type=int,
        default=512 * 1024 * 1024,
        help="Skip MD5 for files larger than this (default 512 MiB)",
    )
    parser.add_argument(
        "--count-excluded",
        action="store_true",
        help="Recursively count files inside pruned directories (slower, exact full-tree total)",
    )
    parser.add_argument(
        "--skip-symlinks",
        action="store_true",
        help="Omit symlink entries instead of recording them (never traversed either way)",
    )
    parser.add_argument(
        "--absolute-paths",
        action="store_true",
        help="Write absolute paths instead of paths relative to --root",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"FAIL: root is not a directory: {root}", file=sys.stderr)
        return 2

    out_path = os.path.abspath(args.out)
    summary_path = os.path.abspath(args.summary) if args.summary else None
    for output in (out_path, summary_path):
        if output and is_within(output, root):
            print(f"FAIL: refusing to write inside the corpus: {output}", file=sys.stderr)
            return 2

    exclude_dirs = set(split_csv_arg(args.exclude_dirs))
    exclude_names = set(split_csv_arg(args.exclude_names))
    hash_exts = parse_exts(args.hash_exts)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    if summary_path:
        os.makedirs(os.path.dirname(summary_path) or ".", exist_ok=True)

    warnings: list[str] = []
    by_ext: Counter[str] = Counter()
    by_ext_bytes: Counter[str] = Counter()
    by_top: Counter[str] = Counter()
    by_top_bytes: Counter[str] = Counter()
    zero_byte: list[str] = []
    hash_groups: dict[str, list[str]] = defaultdict(list)
    hash_sizes: dict[str, int] = {}
    included = 0
    regular_files = 0
    included_bytes = 0
    excluded_files = 0
    excluded_dirs = 0
    symlinks = 0
    dirs_traversed = 0
    raw_entries = 0
    nested_files = 0
    nested_bytes = 0

    columns = [
        "path",
        "top",
        "ext",
        "size",
        "birth",
        "mtime",
        "birth_source",
        "zero",
        "symlink",
    ]
    if hash_exts:
        columns.append("md5")

    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)

        def record(full: str, name: str, stat_result: os.stat_result, is_link: bool) -> None:
            nonlocal included, regular_files, included_bytes, symlinks
            rel = os.path.relpath(full, root)
            written_path = full if args.absolute_paths else rel
            ext = extension_of(name)
            size = 0 if is_link else stat_result.st_size
            included += 1
            if is_link:
                symlinks += 1
            else:
                regular_files += 1
            included_bytes += size
            by_ext[ext] += 1
            by_ext_bytes[ext] += size
            top = rel.split(os.sep, 1)[0] if os.sep in rel else "<root>"
            by_top[top] += 1
            by_top_bytes[top] += size
            if size == 0 and not is_link:
                zero_byte.append(rel)
            birth, birth_source = birth_info(stat_result)
            md5 = ""
            if hash_exts and ext in hash_exts and not is_link:
                if stat_result.st_size > args.hash_max_bytes:
                    warnings.append(f"hash skipped (>{args.hash_max_bytes} bytes): {rel}")
                else:
                    try:
                        md5 = md5_of(full)
                        hash_groups[md5].append(rel)
                        hash_sizes[md5] = stat_result.st_size
                    except OSError as exc:
                        warnings.append(f"hash failed: {rel}: {exc}")
            row = [
                written_path,
                top,
                ext,
                size,
                birth,
                fmt_time(stat_result.st_mtime),
                birth_source,
                int(size == 0),
                int(is_link),
            ]
            if hash_exts:
                row.append(md5)
            writer.writerow(row)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            kept_dirs: list[str] = []
            symlink_dirs: list[str] = []
            for name in sorted(dirnames):
                raw_entries += 1
                full = os.path.join(dirpath, name)
                if name in exclude_dirs:
                    excluded_dirs += 1
                    if args.count_excluded:
                        files, total = count_tree(full)
                        nested_files += files
                        nested_bytes += total
                    continue
                if os.path.islink(full):
                    if args.skip_symlinks:
                        excluded_files += 1
                    else:
                        symlink_dirs.append(name)
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs
            dirs_traversed += len(kept_dirs)

            for name in symlink_dirs:
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except OSError as exc:
                    warnings.append(f"lstat failed: {full}: {exc}")
                    continue
                record(full, name, st, True)

            for name in sorted(filenames):
                raw_entries += 1
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except OSError as exc:
                    warnings.append(f"lstat failed: {full}: {exc}")
                    continue
                is_link = os.path.islink(full)
                if name in exclude_names:
                    excluded_files += 1
                    continue
                if is_link and args.skip_symlinks:
                    excluded_files += 1
                    continue
                record(full, name, st, is_link)

    duplicates = []
    for md5, paths in hash_groups.items():
        if len(paths) > 1:
            duplicates.append(
                {
                    "md5": md5,
                    "size": hash_sizes.get(md5, 0),
                    "count": len(paths),
                    "redundant": len(paths) - 1,
                    "paths": paths[:50],
                    "paths_truncated": len(paths) > 50,
                }
            )
    duplicates.sort(key=lambda item: (-item["redundant"], -item["size"]))

    summary = {
        "root": root,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rule": {
            "follow_symlinks": False,
            "symlinks_recorded": not args.skip_symlinks,
            "exclude_dirs": sorted(exclude_dirs),
            "exclude_names": sorted(exclude_names),
            "hash_exts": sorted(hash_exts),
            "hash_max_bytes": args.hash_max_bytes,
            "count_excluded_recursively": args.count_excluded,
            "paths": "absolute" if args.absolute_paths else "relative_to_root",
            "included_files_counts": "catalog rows, including recorded symlink entries",
        },
        "counts": {
            "catalog_rows": included,
            "regular_files": regular_files,
            "included_bytes": included_bytes,
            "excluded_files_by_name": excluded_files,
            "excluded_dirs_pruned": excluded_dirs,
            "symlink_entries": symlinks,
            "dirs_traversed": dirs_traversed,
            "raw_entries_seen": raw_entries,
            "excluded_files_nested": nested_files if args.count_excluded else None,
            "excluded_bytes_nested": nested_bytes if args.count_excluded else None,
            "raw_files_recursive": (
                regular_files + excluded_files + nested_files
                if args.count_excluded
                else None
            ),
            "zero_byte_files": len(zero_byte),
            "duplicate_groups": len(duplicates),
            "redundant_duplicate_files": sum(d["redundant"] for d in duplicates),
        },
        "by_ext": dict(sorted(by_ext.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_ext_bytes": dict(by_ext_bytes),
        "by_top": dict(sorted(by_top.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_top_bytes": dict(by_top_bytes),
        "zero_byte": zero_byte,
        "duplicates": duplicates,
        "warnings": warnings[:200],
        "warnings_total": len(warnings),
    }

    if summary_path:
        with open(summary_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=False)

    arithmetic_ok = (
        included + excluded_files + excluded_dirs + dirs_traversed == raw_entries
    )
    print(f"OK catalog: {out_path}")
    if summary_path:
        print(f"OK summary: {summary_path}")
    print(
        f"rows={included} regular_files={regular_files} bytes={included_bytes} "
        f"excluded_files={excluded_files} excluded_dirs={excluded_dirs} "
        f"dirs_traversed={dirs_traversed} symlinks={symlinks} raw_entries={raw_entries}"
    )
    if args.count_excluded:
        print(
            f"recursive_excluded={nested_files} raw_files_recursive="
            f"{regular_files + excluded_files + nested_files}"
        )
    print(f"extensions={len(by_ext)} zero_byte={len(zero_byte)} dup_groups={len(duplicates)}")
    if warnings:
        print(f"WARN {len(warnings)} warnings (first 5):", file=sys.stderr)
        for warning in warnings[:5]:
            print(f"  {warning}", file=sys.stderr)
    if not arithmetic_ok:
        print(
            f"FAIL: rows({included}) + excluded_files({excluded_files}) + "
            f"excluded_dirs({excluded_dirs}) + dirs_traversed({dirs_traversed}) "
            f"!= raw({raw_entries})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
