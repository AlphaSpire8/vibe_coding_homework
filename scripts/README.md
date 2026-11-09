# 脚本

- `download_data.py`：根据完整分页清单下载全部 Divvy ZIP；获取两个 NOAA 站点的完整日观测档案、GBFS 站点快照和来源文档。
- `verify_data.py`：离线检查大小、SHA-256、ZIP/GZIP 完整性、JSON 格式与时间覆盖，生成清单和阶段报告。
- `prepare_data.py`：统一历年字段、天气单位与质量标记，生成全量表、过滤子集和整理报告。
- `explore_data.py`：按冻结配置计算质量、描述、时空与天气关联统计，检查汇总守恒及年份边界。
- `plot_exploration.py`：将探索汇总绘制为中文 PNG/PDF。
- `check_weekend_plan.py`：仅用发现年天气与日期检查分层覆盖，生成验证年纯日历周块。
- `validate_weekend.py`：核对冻结方案与输入，执行 2025 年主验证、配对周块重抽样及天气分层实验。
- `plot_weekend.py`：将验证统计绘制为两张中文核心图，输出 PNG/PDF。
- `prepare_deliverables.py`：校验已有汇总并重绘两张汇报图，放入报告和汇报目录，记录来源校验值。
- `__init__.py`：支持从项目根目录以模块方式运行。

在项目根目录执行，统一使用根目录 uv 环境：

```bash
uv sync --locked
uv run python -m scripts.download_data
uv run python -m scripts.verify_data
uv run python -m scripts.prepare_data
uv run python -m scripts.explore_data
uv run python -m scripts.check_weekend_plan
uv run python -m scripts.validate_weekend
uv run python -m scripts.prepare_deliverables
```

- 下载默认 4 并发（`--workers` 可调），支持校验缓存后重跑；`--refresh` 强制更新。默认复用天气与 GBFS 快照，每次重新发现 Divvy 清单；来源发现失败时保留旧清单。
- 完整性检查离线流式读取原始包。整理默认使用 4 线程、4 GiB 内存，成功后替换 `data/processed/`。仅过滤规则改变时可用 `--filter-only`；字段、时间或单位规则改变须完整整理。
- 探索仅分析 2024 年；设计检查只用该年天气与日期；正式验证依冻结配置分析 2025 年。精确版本见各结果目录运行清单，数据口径见 [数据字典](../docs/data_dictionary.md)。
- 绘图需要系统中文字体，服务器已有 Noto Sans CJK。交付制图入口只更新 PDF 配图和校验清单；LaTeX 源码由用户在 TexPage 编译、导出。
