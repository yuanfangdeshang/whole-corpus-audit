# Subagent prompts

Each subagent is read-only. Use one slice per agent and keep the slices disjoint.

## Common preamble

```
You are auditing a large corpus. ROOT: <absolute path>.
This is strictly READ-ONLY: do not create, modify, move, or delete anything under ROOT,
do not install packages, do not start servers, and open databases read-only.
Scratch output is allowed only under <scratch path>.

Your slice: <globs and rough count>.
Do not audit files outside your slice except to resolve a direct reference.

Method:
- Prefer streaming/aggregation over printing raw content.
- For every claim give a concrete path (and line/key when possible) plus the number.
- Distinguish "read in full", "parsed and aggregated", "header only", "sampled", "not opened".
- If two files disagree, report both; do not pick a winner silently.
- If you cannot verify something, put it under "Not verified".

Return a compact report, <= 1200 words, with exactly these sections:
1. Inventory (type -> count -> what it is)
2. Top 10 most important files (path + why + key numbers)
3. Errors / anomalies (path + evidence)
4. Contradictions with written claims (claim, path, conflicting evidence)
5. Coverage boundary (what you did not read and why)
```

## Slice: source code

```
Slice: all .py/.js/.ts/.sh/.R source files (<N>).
Determine: entry points, pipeline stages, what each package does, test counts,
hard-coded absolute paths, non-portable assumptions, duplicate files by md5,
and code paths that would invalidate reported results (leakage, zeroed heads,
disabled branches, parameters never passed to fit).
Report per-directory groups, not per-file prose.
```

## Slice: JSON / run records / configs

```
Slice: all .json (<N>).
Cluster by directory. For each experiment family extract: seeds, status fields,
headline metrics, sample counts, horizon, date ranges.
Flag: complete:true with invalid sibling audit; metrics exactly equal to baseline;
val_n/test_n == 0; missing report; identical MD5 across different seeds;
metric-free "complete" files; duplicated results across kernel sizes/configs.
```

## Slice: logs and text

```
Slice: all .log and .txt (<N>).
Inventory by pattern. Identify: terminal/finish lines, crashes, CLI argument errors,
data-pull success/failure totals, zero-byte logs, truncated logs, retractions.
Quote the exact terminal line for any run you call "completed".
```

## Slice: tabular data (CSV/XLSX/Parquet)

```
Slice: all .csv/.tsv/.xlsx/.parquet (<N>).
For each family: row count, columns, date coverage, delimiter, duplicates,
export caps (e.g. 1,000,000-row spreadsheet limit), empty files,
and whether the table is raw vendor data or a derived model output.
Do not dump rows. Report aggregates and 2-3 representative values.
```

## Slice: documents (PDF/DOCX/PPTX)

```
Slice: all .pdf/.doc/.docx/.pptx (<N>).
Extract title, date, author/affiliation if present, and the quantitative claims.
Separate literature from project deliverables from administrative files.
Flag claims that conflict with the audit artifacts or with later documents.
```

## Slice: notebooks and binary artifacts

```
Slice: .ipynb, .npy/.npz/.pt/.pkl, images, archives (<N>).
Report shapes/dtypes for arrays where cheap; do not load huge tensors.
For archives: member count, total uncompressed size, and whether an extracted
tree already exists that makes the archive redundant.
For notebooks: whether outputs are stored and whether execution order is monotonic.
```

## Slice: odd types

```
Slice: extensionless files, .sqlite/.db, .lock/.pid/.softlock, .sst, .xml, .js bundles (<N>).
Classify each; explain what produced it; note staleness (PIDs that match no process,
locks for interrupted runs, RocksDB internals).
Open SQLite with mode=ro&immutable=1; report schema and row counts, never start the owning app.
```

## Synthesis rules for the main agent

- Keep only the returned reports; do not re-run their searches.
- Spot-check at least two claims per agent.
- Convert every count into the coverage table before writing prose.
- When an agent's count differs from the catalog, the catalog wins unless the agent can name the exact files it counted.
