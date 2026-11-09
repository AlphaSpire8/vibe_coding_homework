# 测试

- `test_data_tools.py`：检查不完整下载、缓存损坏、ZIP CRC、历史包日期范围和天气质量标记。

在根目录执行：

```bash
uv run python -m unittest discover -s tests -v
```

测试使用临时文件和本地 HTTP 服务，不访问数据供应商。
