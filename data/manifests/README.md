# 数据清单

- `downloads.json`：每个文件的来源 URL、获取时间、大小、SHA-256 和服务器版本信息。
- `zip_contents.csv`：ZIP 内成员的类型、大小、CRC 和 CSV 字段名，不含行程记录。
- `coverage.json`：完整性检查、行程时间边界、月份覆盖、天气缺失与质量标记范围。
- `preparation.json`：整理规则、输入版本、记录数核对、字段映射与输出文件校验值。
- `preparation_by_file.csv`：每个行程 CSV 标准化与逐项过滤后的记录数。
- `prepared_schema.json`：整理后各表的字段名和类型。

清单对应本次本地快照。重新下载上游更新后，应再次执行完整性检查。
