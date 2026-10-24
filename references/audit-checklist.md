# Audit checklist

## Before scanning

- [ ] Target root confirmed; scratch dir is outside it.
- [ ] Read-only intent stated; no server/database writes planned.
- [ ] Exclusion rule written down (dirs, names, hidden files, symlinks).
- [ ] Expected scale recorded (`du -sh`, rough file count) for sanity checks.

## Catalog

- [ ] `build_catalog.py` exit code 0.
- [ ] `catalog_rows + excluded_files + excluded_dirs + dirs_traversed == raw_entries_seen`.
- [ ] Per-extension counts sum to `catalog_rows`.
- [ ] Per-top-directory counts sum to `catalog_rows`.
- [ ] Extensionless files are in their own bucket, not dropped.
- [ ] Zero-byte list reviewed; every zero-byte file that is cited as evidence is flagged.
- [ ] Duplicate MD5 groups reviewed; mirrored directories noted.
- [ ] `birth` is labelled with its source and not treated as creation time.

## Tabular metadata

- [ ] Count of `.csv` rows in catalog equals `csv_meta.tsv` rows minus header.
- [ ] Delimiters detected (`;`, `\t`, `|` files are not misread as single-column).
- [ ] BOM tolerated; undecodable bytes recorded as `error`, not crashed.
- [ ] Row counts compared against known export caps.
- [ ] Header hashes used to detect schema drift, not copied from prose.

## Text bundle

- [ ] `included + duplicate + empty + unreadable + too_large + binary_skipped`
      equals the number of files with the selected extensions.
- [ ] Marker line ranges in the index match the bundle.
- [ ] Bundle read in 400–600 line chunks; every `FILE:` marker accounted for.
- [ ] Large or low-value documents marked `sampled` rather than `read`.

## Semantic screening

- [ ] Every `complete: true` checked against sibling audit/status files.
- [ ] Results exactly equal to baseline are flagged.
- [ ] `alpha = 0`, best epoch 0/1, `val_n = 0`, `test_n = 0` flagged.
- [ ] Missing `report.json` / metric-free completion flagged.
- [ ] Cross-seed MD5 duplicates flagged.
- [ ] Parameters claimed in prose checked for actually being passed to the fit.
- [ ] Truncated logs and export caps flagged.
- [ ] Conflicting definitions across pipeline generations listed.

## Reconciliation

- [ ] Every number quoted from a document re-derived from the artifact.
- [ ] Earlier totals that do not reproduce are listed with both values and methods.
- [ ] Archive vs extracted tree counted once, with the redundancy noted.
- [ ] Duplicate copies never presented as corroboration.

## Final report

- [ ] Coverage table has `Type | Count | Coverage method | Evidence | Boundary`.
- [ ] Every evidence path exists; `verify_coverage.py` exits 0.
- [ ] "Coverage method" uses one of: full text, parsed+aggregated, header/schema,
      metadata, duplicate of X, not opened.
- [ ] Boundary section says what was not read and what the audit does not prove.
- [ ] Contradictions section lists both artifacts with paths.
- [ ] Corrections section is included if any earlier number changed.
- [ ] No unqualified total; the inclusion rule is adjacent to every count.
