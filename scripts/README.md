# 数据工具

- `download_data.py`：根据完整分页清单下载全部 Divvy ZIP；获取两个 NOAA 站点的完整日观测档案、GBFS 站点快照和来源文档。
- `verify_data.py`：离线检查大小、SHA-256、ZIP/GZIP 完整性、JSON 格式与时间覆盖，生成清单和阶段报告。
- `prepare_data.py`：统一历年字段、天气单位与质量标记，生成全量表、过滤子集和整理报告。
- `explore_data.py`：按冻结配置计算质量、描述、时空与天气关联统计，检查汇总守恒及年份边界。
- `plot_exploration.py`：将探索汇总绘制为中文 PNG/PDF。
- `check_weekend_plan.py`：仅用发现年天气与日期检查分层覆盖，生成验证年纯日历周块。
- `validate_weekend.py`：核对冻结方案与输入，执行 2025 年主验证、配对周块重抽样及天气分层实验。
- `plot_weekend.py`：将验证统计绘制为两张中文核心图，输出 PNG/PDF。
- `__init__.py`：支持从项目根目录以模块方式运行。

在项目根目录执行，统一使用根目录 uv 环境：

```bash
uv sync --locked
uv run python -m scripts.download_data
uv run python -m scripts.verify_data
uv run python -m scripts.prepare_data
uv run python -m scripts.explore_data
```

下载支持失败后重跑，复用大小与校验值匹配的文件；单个文件通过临时文件写入。默认并发数为 4，可用 `--workers` 调整。`--refresh` 重新获取全部文件；默认保留已下载的天气与 GBFS 快照，Divvy 文件列表每次重新获取。

来源发现完成前保留旧清单记录；分页或 GBFS 获取失败、中断时，旧快照的校验值与获取时间仍可复用。

验证不解压保留第二份数据，通过流式读取检查原始包。完整性通过不代表记录已完成清洗；月份覆盖依据来源包名，仅扫描首尾包确认行程日期边界。

整理使用 DuckDB，默认最多 4 个线程与 4 GiB 数据库内存；逐个临时解压 CSV。全部处理和记录数核对成功后替换 `data/processed/`，失败时保留原有整理结果。规则与口径见 `docs/data_dictionary.md`。

仅调整过滤规则时，可执行 `uv run python -m scripts.prepare_data --filter-only`，核验并复用已有标准化表，重新生成过滤子集和审计。字段映射、时间解析或单位规则改变时须完整执行，不能使用该选项。

探索读取 `configs/exploration.json`，输出至 `results/eda/`，使用 Matplotlib 与中文系统字体（服务器已安装 Noto Sans CJK）。2025 年仅复用已有质量计数，行为及天气关系统计限定在 2024 年；范围变更须先讨论。图表按日聚合天气关联，研究报告与候选问题由阅读结果后撰写。

验证方案的可行性检查执行 `uv run python -m scripts.check_weekend_plan`，读取 `configs/weekend_validation.json` 和已有探索日表，核对年份及校验值后输出至 `results/study_plan/`；不计算行程效应，不读取 2025 年行程或天气。

正式验证执行 `uv run python -m scripts.validate_weekend`，依照已通过 PR #5 的配置，输出至 `results/validation/`。运行前核对全部整理文件的 SHA-256 及发现年参考结果；仅分析 2025 年，沿用筛选口径，不覆盖探索结果。配置中的状态保留为制定时值，实际执行状态写入结果。
