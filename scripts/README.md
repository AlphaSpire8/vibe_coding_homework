# 数据工具

- `download_data.py`：根据完整分页清单下载全部 Divvy ZIP；获取两个 NOAA 站点的完整日观测档案、GBFS 站点快照和来源文档。
- `verify_data.py`：离线检查大小、SHA-256、ZIP/GZIP 完整性、JSON 格式与时间覆盖，生成清单和阶段报告。
- `prepare_data.py`：统一历年字段、天气单位与质量标记，生成全量表、过滤子集和整理报告。
- `__init__.py`：支持从项目根目录以模块方式运行。

在项目根目录执行，统一使用根目录 uv 环境：

```bash
uv sync --locked
uv run python -m scripts.download_data
uv run python -m scripts.verify_data
uv run python -m scripts.prepare_data
```

下载支持失败后重跑，复用大小与校验值匹配的文件；单个文件通过临时文件写入。默认并发数为 4，可用 `--workers` 调整。`--refresh` 重新获取全部文件；默认保留已下载的天气与 GBFS 快照，Divvy 文件列表每次重新获取。

来源发现完成前保留旧清单记录；分页或 GBFS 获取失败、中断时，旧快照的校验值与获取时间仍可复用。

验证不解压保留第二份数据，通过流式读取检查原始包。完整性通过不代表记录已完成清洗；月份覆盖依据来源包名，仅扫描首尾包确认行程日期边界。

整理使用 DuckDB，默认最多 4 个线程与 4 GiB 数据库内存；逐个临时解压 CSV。全部处理和记录数核对成功后替换 `data/processed/`，失败时保留原有整理结果。规则与口径见 `docs/data_dictionary.md`。

仅调整过滤规则时，可执行 `uv run python -m scripts.prepare_data --filter-only`，核验并复用已有标准化表，重新生成过滤子集和审计。字段映射、时间解析或单位规则改变时须完整执行，不能使用该选项。
