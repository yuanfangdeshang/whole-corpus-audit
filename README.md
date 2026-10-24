# whole-corpus-audit

面向超大混合代码/数据仓库的「全量文件覆盖审计」Skill。它回答的不是“我大致看过”，而是：**有哪些文件、里面是什么、每个文件到底怎么读的、哪些没读**。

## 适用场景

- 仓库有 1 万以上文件，代码、CSV、JSON、日志、PDF、Office、二进制混杂
- 用户要求“覆盖所有文件资产”“每个文件都要读到”“全量审计”
- 需要在有限上下文中完成审计，并给出可复核的证据表

不适用：单文件审查、普通代码搜索、小仓库。

## 安装

```bash
git clone https://github.com/yuanfangdeshang/whole-corpus-audit \
  ~/.qoder/skills/whole-corpus-audit
```

重启会话或执行 `/skills reload`，然后用 `/whole-corpus-audit` 调用。

## 快速开始

```bash
OUT=/tmp/audit
mkdir -p "$OUT"

# 1. 全量清单（只读，自动排除 .git / node_modules / .venv 等）
python3 scripts/build_catalog.py \
  --root /path/to/corpus \
  --out "$OUT/catalog.csv" \
  --summary "$OUT/catalog_summary.json" \
  --hash-exts .csv,.json --count-excluded

# 2. 表格指纹
python3 scripts/build_csv_meta.py \
  --catalog "$OUT/catalog.csv" \
  --out "$OUT/csv_meta.tsv" --root /path/to/corpus

# 3. 文本打包 + MD5 去重（按 400–600 行分块阅读）
python3 scripts/make_text_bundle.py \
  --catalog "$OUT/catalog.csv" \
  --out "$OUT/all_text.txt" \
  --index "$OUT/all_text_index.json" --root /path/to/corpus

# 4. 核对算术与证据路径
python3 scripts/verify_coverage.py \
  --catalog "$OUT/catalog.csv" \
  --summary "$OUT/catalog_summary.json" \
  --csv-meta "$OUT/csv_meta.tsv" \
  --bundle-index "$OUT/all_text_index.json" \
  --root /path/to/corpus
```

随后按文件类型拆分给多个只读子代理并行审计，提示词模板见 `references/subagent-prompts.md`。

## 产出

| 文件 | 内容 |
|---|---|
| `catalog.csv` | 每个文件一行：路径、类型、大小、时间、零字节、软链接、MD5 |
| `catalog_summary.json` | 计数口径、按扩展名/目录汇总、零字节清单、重复组 |
| `csv_meta.tsv` | 行数、分隔符、表头、表头哈希、坏行抽样、错误 |
| `all_text.txt` + index | 带 `FILE:` 标记的文本合集与行号索引 |
| `coverage.tsv` | 完成表：`type / count / coverage_method / evidence / boundary` |

## 关键原则

- **只读**：不修改目标语料，不启动其依赖的服务，数据库以只读方式打开
- **先定计数口径**：排除项、软链接、隐藏文件必须写清楚，否则总数不可复核
- **出生时间 ≠ 创建时间**：文件系统的 `birthtime` 常继承自复制或解压
- **`complete: true` 不等于有效**：要核对同级审计文件、基线数值、空验证集
- **冲突并列报告**：两个数字对不上时列证据，不取平均、不静默选一个
- **不宣称逐行读完**：覆盖方式必须区分“全文 / 解析汇总 / 仅表头 / 抽样 / 未打开”

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/build_catalog.py` | 只读清单、MD5、零字节、重复组、口径核算 |
| `scripts/build_csv_meta.py` | 分隔符嗅探、BOM 容错、行数、表头哈希 |
| `scripts/make_text_bundle.py` | 文本打包、去重、行号索引 |
| `scripts/verify_coverage.py` | 算术核对与证据路径检查，失败退出码 1 |

全部脚本仅使用 Python 标准库（3.9+），并拒绝向被审计目录内写输出。

## License

MIT
