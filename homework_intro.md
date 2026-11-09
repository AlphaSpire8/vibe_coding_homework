# 完成 vibe coding 作业

我这学期上了一门课“人工智能与科学研究”。现在我要完成这门课的作业。

## 作业的标题

vibe coding

## 作业内容

课程提供统一的数据集，不预先指定研究问题。

同学们需利用 AI 辅助编程（Vibe Coding）开展探索性数据分析，自主发现一个值得进一步研究的问题，并设计实验进行初步验证。

具体要求：

1. 数据探索： 利用 Vibe Coding 完成至少三类分析任务，包括数据清洗、统计分析、可视化、异常检测、聚类、预测建模、相关性分析等。
2. 问题发现： 从数据中发现有价值的异常现象或潜在规律，提出一个明确、可验证的研究假设。
3. 实验验证： 针对研究假设，设计并完成至少一项额外实验，分析假设是否获得初步支持。
4. AI 使用分析： 说明哪些发现来自 AI 的辅助，哪些判断由自己独立完成。

## 提交内容

1. 代码： 完整、可运行的程序代码。
2. PPT： 展示研究思路、核心发现、验证实验及结论，重点展示最关键的两张图。
3. 完整报告： 详细记录数据分析过程、问题发现、研究假设、实验设计、结果分析及 AI 使用情况。

## 课程提供的数据集信息

芝加哥 Divvy 共享单车站点级出行 + NOAA 天气

两个官方入口：

- **Divvy 行程/站点**：`https://www.divvybikes.com/system-data/`

  选 **Trips** 历史月度 CSV；**Stations** 建议用 GBFS/JSON 或页面提供的静态文件。若页面改版，去 Chicago Data Portal 搜 “Divvy trips / Divvy stations” 做镜像。

- **NOAA 天气**：`https://www.ncei.noaa.gov/access/past-weather/` 或 CDO

  建议下载 **Daily Summaries**，站点用 **Chicago O’Hare USW00094846**，备选用 Midway **USW00014819**。需要小时风雨再取 ISD：`https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database`

拼接方式：`trips.started_at` 截到日/小时，接 `PRCP/SNWD/SNOW/AWND/TMAX/TMIN`；注意 **2020 疫情异常**、站点迁移/改名导致 `station_id` 不稳定，以及 Divvy 许可不可再分发，请注明来源与日期版本。

