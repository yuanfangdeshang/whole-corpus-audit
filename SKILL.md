---
name: whole-corpus-audit
description: Audit a very large mixed code/data corpus end to end — build a full file catalog with hashes, fingerprint every CSV, bundle and deduplicate text, dispatch parallel read-only subagents per file type, reconcile counts, screen artifact semantics, and emit a type-to-coverage-to-evidence completion table with explicit boundaries. Use when the user asks for 全量文件覆盖审计, 覆盖所有文件资产, 每个文件都要读到, whole-corpus audit, every-file inventory, or when a repository has 10k+ mixed files (code, CSV, JSON, logs, PDF, office, binaries) that cannot fit in one context; not for single-file review, targeted code search, or small repos.
---

# Whole-Corpus Audit

## Overview

A whole-corpus audit answers one question with evidence: *which files exist, what is in them, and which ones did we actually read?* It replaces "I looked around the repo" with a reproducible catalog, per-type extraction, parallel reading, and an auditable coverage table. The target corpus is strictly read-only; every claim in the final report must point at a file and a number that a reviewer can re-derive.

## Non-negotiables

1. **Read-only on the target.** No writes, renames, deletes, installs, migrations, or service starts inside the corpus. Put all outputs in a scratch directory outside it (e.g. `/tmp/<corpus>-audit`). Open SQLite with `file:...?mode=ro&immutable=1`; never start the app that owns a data store (e.g. an experiment tracker UI) just to read it.
2. **Fix the counting basis before counting.** Different tools produce different totals because of symlinks, hidden files, vendor directories, `.DS_Store`, and hard links. State the inclusion rule, then use one rule everywhere. Never print an unqualified "N files" that you cannot reproduce.
3. **Do not claim line-by-line reading.** For every file type record *how* it was covered: full text read, parsed and aggregated, header+schema only, sampled, or structurally present but content unread. "Covered" is not "read".
4. **Timestamps are evidence, not truth.** Filesystem `birthtime` is often inherited from a copy or archive extraction, and `mtime` can be reset. Label which clock you used. Derive chronology from content (log lines, document dates, in-file timestamps) before asserting when something happened.
5. **`complete`, `success`, `final` in filenames or JSON are claims, not states.** Verify siblings: an audit/status file may declare the same run invalid; a "completed" model may be numerically identical to the baseline; a `report.json` may contain no metrics.
6. **Report conflicts, do not average them.** When two artifacts disagree (counts, definitions, metric signs, budgets), list both with paths. Reconciliation is a finding.

## Phase 0 — Scope and counting basis

Before scanning, write down in one paragraph:

- root path and whether symlinks are followed;
- excluded directories/names (vendor dirs, VCS, caches, editor junk) and whether they are counted anywhere;
- whether hidden files count;
- whether duplicate copies count as separate files;
- the exact command/tool that defines the total.

Then produce the catalog. `scripts/build_catalog.py` records raw vs excluded vs included counts and per-extension/per-top-directory totals, so the basis is embedded in the artifact rather than in prose.

## Phase 1 — Catalog

```bash
python3 scripts/build_catalog.py --root /path/to/corpus --out /tmp/audit/catalog.csv \
  --summary /tmp/audit/catalog_summary.json --hash-exts .csv,.json,.py
```

Output columns: `path,top,ext,size,birth,mtime,birth_source,zero,symlink[,md5]`. The summary reports included/excluded/raw counts and bytes, top extensions, top directories, zero-byte files, and MD5 duplicate groups.

Rules:
- Keep extensionless files in a separate bucket (`<none>`), do not silently drop them. They are often databases, license texts, lock files, or RocksDB internals.
- Hash only the extensions you will deduplicate; hashing tens of GB is slow and usually unnecessary.
- Treat the catalog as the denominator for everything that follows.

## Phase 2 — Text bundling and deduplication

Reading hundreds of small docs one call at a time burns the context. Bundle them:

```bash
python3 scripts/make_text_bundle.py --catalog /tmp/audit/catalog.csv \
  --root /path/to/corpus --out /tmp/audit/all_text.txt \
  --index /tmp/audit/all_text_index.json --exts .md,.txt,.rst,.tex
```

The bundle inserts `FILE: <path>` markers plus an index of start/end lines, skips binary content (NUL byte), skips files over a size cap, and records `included / duplicate / empty / unreadable / too_large / binary_skipped`. Then read it with `Read` and `offset/limit` in chunks of roughly 400–600 lines — never dump the whole bundle into a terminal command whose output will be truncated.

Deduplicate before reading: identical MD5 files (repeated report copies, mirrored directories, per-run source snapshots) only need to be read once. Say so in the coverage table.

For formats that need extraction, prefer local converters already installed:
- PDF: `pdftotext -layout`; fall back to a Python PDF library if present.
- `.doc`: `textutil -convert txt` on macOS.
- `.docx`: unzip `word/document.xml` and strip tags.
- XLSX: `openpyxl` with `read_only=True`; never load a 1M-row workbook fully.
- JSON/log/CSV: stream line by line; aggregate, do not print raw.

## Phase 3 — Parallel type-slice subagents

Split by **file type or artifact family**, not by directory alphabet. Each subagent gets:

- the corpus root, an explicit statement that it is read-only;
- its assigned globs and a rough count;
- the exact output contract (see `references/subagent-prompts.md`);
- a length cap and a rule to return path + number for every claim.

Typical slices: source code; JSON/run records; logs + text; tabular data (CSV/XLSX/Parquet); documents (PDF/DOCX/PPTX); notebooks/binary artifacts; odd types (no extension, sqlite, lock, pid).

Do not also perform the same searches yourself while a subagent owns that slice — that duplicates work and doubles context use. Keep only the distilled reports in the main context.

## Phase 4 — Reconcile the numbers

Before synthesis, cross-check:

- catalog total = sum of per-extension totals = sum of per-directory totals;
- CSV metadata row count equals catalog CSV count (minus excluded);
- text bundle included + duplicate + empty + unreadable + too_large + binary_skipped equals the count of selected extensions;
- the total in the catalog equals the "raw" total minus explicitly excluded files;
- any figure quoted from a document, dashboard, or earlier report is re-derived from the artifact, not copied.

When a number does not reproduce, report the discrepancy and the method that produced each value. Do not adjust the artifact to match the claim.

## Phase 5 — Semantic artifact screening

Structural presence is not validity. A run directory can contain a "complete" marker and still be unusable. Screen for at least:

- **Leakage/audit verdicts**: a sibling `findings.json`, `AUDIT_STATUS.json`, or README that withdraws the result.
- **Degenerate outputs**: metric exactly equal to the baseline; `alpha = 0`; best epoch 0 or 1; empty validation/test sets (`val_n = 0`, `test_n = 0`); missing `report.json`.
- **Duplicated evidence**: same JSON/MD5 across different seeds or experiments; identical metrics across supposedly independent configurations; a parameter that was never actually passed to the fit.
- **Metric-free completion**: files that only say `complete: true` or contain forecasts without scores.
- **Truncation**: export caps (e.g. spreadsheet row limits), interrupted logs with no terminal line, zero-byte logs whose run is cited as evidence.
- **Definition drift**: the same column/filename meaning different things across generations (e.g. one cache filters a different curve type than another).

Record each finding with path, the conflicting values, and the decision you applied in the final table.

## Phase 6 — Completion table

The deliverable is a table, not a narrative. Required columns:

| Type / family | Count | Coverage method | Evidence (path) | Boundary |
|---|---|---|---|---|

Rules:
- "Coverage method" names the concrete operation: `full text`, `parsed + aggregated`, `header/schema only`, `duplicate of X (md5)`, `metadata only`, `not opened`.
- "Evidence" is a real path (plus line or key where useful).
- "Boundary" states what was *not* established: unread binary payloads, sampled rows, missing external sources, unresolved contradictions.
- Add a short "numbers that changed" section when earlier reports were corrected, and a "contradictions" section listing conflicting artifacts side by side.
- Close with an explicit statement of what the audit does **not** prove (e.g. statistical validity, business correctness, completeness of external data).

## Artifact contract

Keep these in the scratch directory and reference them from the report:

```
catalog.csv              # one row per included file
catalog_summary.json     # counting basis, per-ext/per-dir totals, zero-byte, dup groups
csv_meta.tsv             # path,size,lines,delimiter,header,head_hash,ragged_sample,error
all_text.txt             # FILE: markers + content
all_text_index.json      # line ranges, dedup/skip buckets
coverage.tsv             # the completion table, machine-readable
report.md                # prose synthesis + boundaries
```

## Scripts

- `scripts/build_catalog.py` — read-only inventory, hashes, totals, zero-byte, duplicates.
- `scripts/build_csv_meta.py` — delimiter sniffing, header hash, line counts with BOM/bad-byte tolerance.
- `scripts/make_text_bundle.py` — deduplicated text bundle + line index.
- `scripts/verify_coverage.py` — arithmetic and evidence checks; exits non-zero on failure.

All four use only the Python standard library (3.9+) and never modify the corpus.

## References

- `references/workflow.md` — detailed phase-by-phase procedure and chunking strategy.
- `references/subagent-prompts.md` — copy-ready prompts and output contract for type slices.
- `references/audit-checklist.md` — pre-flight, reconciliation, and final-report checklist.

## Failure modes to avoid

- Reporting a single "total files" without the inclusion rule.
- Reading every file in the main context until compaction erases the evidence.
- Trusting `complete: true`, a filename, or a README summary over the sibling audit file.
- Counting a directory's contents from a document instead of from the filesystem.
- Treating a compressed archive and its extracted tree as two independent datasets.
- Presenting a duplicate copy as corroborating evidence.
- Calling header-only inspection "read".
