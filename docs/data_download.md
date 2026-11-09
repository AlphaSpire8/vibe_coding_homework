# 数据下载与完整性检查

检查时间（UTC）：2026-11-09T11:25:30+00:00。本文件由 `scripts.verify_data` 生成。

- 官方历史归档：97 个 ZIP，全部通过大小、SHA-256 与完整 ZIP CRC 检查。
- 全部归档文件：110 个，合计 1.829 GiB。
- 行程时间边界：2013-06-27 至 2026-10-31，依据首尾来源包中的出发日期。
- 行程日期跨度含 161 个月；缺少对应来源包的月份：无。
- 历史站点 CSV：8 份，保存在原始 ZIP 内；当前 GBFS station_information 快照含 2079 个站点。

## 天气覆盖

天气使用 NOAA GHCN-Daily 的完整站点档案；下表仅检查行程日期范围内的覆盖。可用天数包含带质量标记的值，后续清洗需单独判断；缺失包括未提供或值为 -9999。

| 站点 | 档案日期范围 | 观测记录数 |
| --- | --- | --- |
| USW00094846 | 1946-10-09 至 2026-11-04 | 386002 |
| USW00014819 | 1997-05-01 至 2026-11-04 | 104640 |

| 站点 | 变量 | 可用天数 / 总天数 | 质量标记天数 | 缺失天数 |
| --- | --- | --- | --- | --- |
| USW00094846 | PRCP | 4875 / 4875 | 0 | 0 |
| USW00094846 | SNWD | 4875 / 4875 | 0 | 0 |
| USW00094846 | SNOW | 4875 / 4875 | 0 | 0 |
| USW00094846 | AWND | 4875 / 4875 | 0 | 0 |
| USW00094846 | TMAX | 4875 / 4875 | 0 | 0 |
| USW00094846 | TMIN | 4875 / 4875 | 0 | 0 |
| USW00014819 | PRCP | 4865 / 4875 | 0 | 10 |
| USW00014819 | SNWD | 855 / 4875 | 0 | 4020 |
| USW00014819 | SNOW | 208 / 4875 | 0 | 4667 |
| USW00014819 | AWND | 4871 / 4875 | 0 | 4 |
| USW00014819 | TMAX | 4874 / 4875 | 4 | 1 |
| USW00014819 | TMIN | 4874 / 4875 | 3 | 1 |

## 核验范围与来源

- 文件清单、下载时间、来源 URL 和校验值见 [downloads.json](../data/manifests/downloads.json)；包内文件及字段见 [zip_contents.csv](../data/manifests/zip_contents.csv)。
- 天气缺失日期范围与检查详情见 [coverage.json](../data/manifests/coverage.json)。
- 月份检查依据官方包名；仅扫描首尾包确定实际日期边界，尚未逐行核实所有年份、去重或清洗。
- GBFS 是获取时的当前状态，不作为历史站点名录。

来源：[Divvy](https://divvybikes.com/system-data)、[官方归档](https://divvy-tripdata.s3.amazonaws.com/)、[NOAA GHCN-Daily](https://www.ncei.noaa.gov/pub/data/ghcn/daily/)、[NOAA 格式说明](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme-by_station.txt)。原始数据和来源文档保存在服务器。
