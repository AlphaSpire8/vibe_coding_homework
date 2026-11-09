# 数据工具

- `download_data.py`：根据完整分页清单下载全部 Divvy ZIP；获取两个 NOAA 站点的完整日观测档案、GBFS 站点快照和来源文档。
- `verify_data.py`：离线检查大小、SHA-256、ZIP/GZIP 完整性、JSON 格式与时间覆盖，生成清单和阶段报告。
- `__init__.py`：支持从项目根目录以模块方式运行。

在项目根目录执行，统一使用根目录 uv 环境：

```bash
uv sync --locked
uv run python -m scripts.download_data
uv run python -m scripts.verify_data
```

下载支持失败后重跑，复用大小与校验值匹配的文件；单个文件通过临时文件写入。默认并发数为 4，可用 `--workers` 调整。`--refresh` 重新获取全部文件；默认保留已下载的天气与 GBFS 快照，Divvy 文件列表每次重新获取。

验证不解压保留第二份数据，通过流式读取检查原始包。完整性通过不代表记录已完成清洗；月份覆盖依据来源包名，仅扫描首尾包确认行程日期边界。
