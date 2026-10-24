# Workflow detail

## 0. Pre-flight

- Confirm the corpus is not a git repo you might accidentally modify, and that all work will happen in a scratch root such as `/tmp/<name>-audit`.
- Record the OS, filesystem, and Python version; `birthtime` semantics differ across platforms.
- List top-level directories with `du -sh` and file counts before scanning, so you can sanity-check the catalog's totals.
- Identify expected heavy or vendor directories (`.git`, `node_modules`, `.venv`, `site-packages`, `.deps`, `__pycache__`, caches) and decide the exclusion rule once.

## 1. Catalog and the counting basis

`build_catalog.py` walks the tree without following symlinks. It writes:

- `path` — absolute or root-relative (choose one and keep it stable);
- `top` — first path segment, used for per-directory reconciliation;
- `ext` — lowercase extension, or `<none>`;
- `size`, `birth`, `mtime`, `birth_source`;
- `zero` — size == 0;
- `symlink` — entry is a link (recorded, not traversed);
- `md5` — only for extensions passed in `--hash-exts`.

The summary is the authoritative record of the inclusion rule: raw entries, excluded entries, catalog rows, traversed directories, total bytes, per-extension and per-top-directory counts, zero-byte list, duplicate groups. `catalog_rows` counts rows (including recorded symlink entries); `regular_files` excludes symlinks.

Sanity checks:

- `catalog_rows + excluded_files + excluded_dirs + dirs_traversed == raw_entries_seen`;
- `regular_files + excluded_files + excluded_files_nested == raw_files_recursive`;
- sum of per-ext counts == catalog rows;
- number of `.csv` rows in the catalog equals the number of lines in `csv_meta.tsv` minus its header.

## 2. Text corpus

Bundling rules:

- Insert `FILE: <path>` before each unique file and `END: <path>` after it; write both marker line numbers into the index.
- Compute MD5 of file bytes first; on a repeat, write only `FILE: <path> (duplicate of <first>)` and record it in the `duplicate` bucket.
- Detect binary with a NUL byte in the first 8 KiB.
- Skip files above `--max-bytes` (default 8 MiB) and record them as `too_large`; they must be handled separately.
- Preserve line numbering by never wrapping or re-indenting content.

Reading strategy:

- Read the index first, then read the bundle in 400–600 line chunks.
- Keep a running list of `FILE:` markers already read and their line ranges.
- When a document is long but low-value (licenses, generated API dumps), read the first and last pages and mark it `sampled` in the coverage table.

## 3. Per-type extraction

| Type | Method | What to record |
|---|---|---|
| `.py`, `.js`, `.sh` | read source; count tests/functions by grep | entry points, side effects, hard-coded paths, test counts |
| `.json` | stream/parse; aggregate keys | run status, metrics, seeds, completion markers |
| `.log` | tail + grep for error/finish markers | terminal line, failure counts, truncation |
| `.csv` | header + columns + row count + ranges | symbolic delimiter (comma/semicolon/tab/pipe), header hash, date span, missingness |
| `.xlsx` | `openpyxl(read_only=True)` | sheet count, row cap, truncation |
| `.pdf` | `pdftotext -layout` | page count, title, claimed numbers |
| `.doc/.docx` | `textutil` / XML unzip | title, dates, claims |
| `.ipynb` | JSON parse; list cells/outputs | whether outputs are stored, execution order |
| `.sqlite` | read-only URI; `.tables`, counts | schema, row counts, zero-row tables |
| images/binaries | metadata only | dimensions/size; mark content unread |
| extensionless | `file` + first bytes | classify (license, lock, DB, PID) |

## 4. Parallel decomposition

- One agent per artifact family. Keep each agent's slice disjoint and explicit.
- Give every agent the same output contract and a hard word cap.
- Ask for a "contradictions" section and a "not verified" section; those are the highest-value parts.
- Do not ask an agent to re-derive totals you already have; ask it to explain what the files *mean*.
- After the agents return, spot-check two claims per agent against the files yourself. Agents summarize intent, not always what they did.

## 5. Reconciliation and corrections

Maintain a `corrections.md` scratch file with rows: `claim | source | reproduced? | corrected value | method`. This becomes the "numbers that changed" section.

Common sources of drift:

- counting before/after excluding vendor dirs;
- counting archive members vs extracted files;
- counting directories as files;
- extension case (`.CSV` vs `.csv`);
- hidden files;
- symlink duplicates;
- one concept measured on two different samples (e.g. validation window vs test window);
- definitions that changed between pipeline generations.

## 6. Final report shape

1. Scope and counting basis (one paragraph + the exclusion list).
2. Inventory table by type/family with counts and sizes.
3. The completed coverage table.
4. Corrections to earlier claims.
5. Contradictions between artifacts.
6. What the audit does not prove.

Keep the prose short; the tables and paths carry the evidence.
