# 研究与 AI 使用记录

作业已提交。本文保留最终决策与实际分工，逐次修改见 [PR 历史](https://github.com/AlphaSpire8/vibe_coding_homework/pulls)。统计线索与计算结果归为 AI 辅助发现；用户承担选题、规则确认、审阅及材料修改。

| 环节 | AI 辅助工作 | 用户判断与行动 | 依据 |
| --- | --- | --- | --- |
| 数据获取 | 编写下载与完整性检查，归档 97 个行程 ZIP、天气和站点资料 | 选择下载全部可获取历史，要求分类存放 | [下载报告](data_download.md) |
| 清洗与复核 | 标准化字段、标记异常、执行过滤；按自动 review 修复清单缓存问题并测试 | 确认时长、站点完整性、重复及车型规则，通过 PR 审阅实现 | [整理报告](data_preparation.md)、[PR #2](https://github.com/AlphaSpire8/vibe_coding_homework/pull/2) |
| 年度划分 | 建议发现年与留出年，执行四类探索 | 确认 2024 年探索、2025 年验证；全历史只作质量背景 | [探索范围](exploration_scope.md)、[探索报告](exploratory_analysis.md) |
| 选题 | 归纳模式并提出下表三个候选 | 选择问题 A | 下表 |
| 实验设计 | 拟定主指标、周块重抽样与天气分层；仅用 2024 年协变量检查覆盖 | 选择月份×10°C×三类降水、每格两种日类型各至少 2 天，审阅后固定方案 | [验证方案](weekend_study_protocol.md)、[PR #5](https://github.com/AlphaSpire8/vibe_coding_homework/pull/5) |
| 验证 | 按固定规则计算主结果与额外实验，生成图表和运行清单 | 审阅并接受验证阶段交付 | [验证结果](weekend_validation_results.md)、[PR #6](https://github.com/AlphaSpire8/vibe_coding_homework/pull/6) |
| 汇报与报告 | 整理中文 LaTeX、两张核心图与讲稿，检查编译、引用和版式 | 确定中文短汇报，在 TexPage 编译并精简材料，确认封面隐藏日期 | [交付材料](../deliverables/)、[PR #7](https://github.com/AlphaSpire8/vibe_coding_homework/pull/7)、[PR #8](https://github.com/AlphaSpire8/vibe_coding_homework/pull/8) |
| 提交与归档 | 合并重复过程文档、清理本作业临时缓存 | 已提交作业，确认仅清理本作业缓存 | 用户对话 |

## 选题记录

以下线索均来自 2024 年；仅 A 进入验证，B、C 保留为未验证候选。

| 候选问题 | 探索线索 | 处理 |
| --- | --- | --- |
| A. 非会员的周末/平日日均行程比是否高于会员？ | 1.58 对 0.77；12 个月均是非会员更高 | 用户选择，按预设方案在 2025 年验证 |
| B. 同一天比较时，非会员的中位行程时长是否更长？ | 349/366 天更长，日中位时长差的中位数为 3.31 分钟 | 未验证，车型构成仍需检查 |
| C. 雨日相对行程量下降是否在非会员中更大？ | 月及日类型分层后的雨日/无雨日指数比为 0.760 对 0.831 | 未验证，温度和样本选择等混杂未充分处理 |

报告与汇报曾在独立目录中用 Tectonic 0.17.0（XeTeX）检查，分别为 8 页、7 页，日志无警告。本作业的编译器下载、缓存、预览与修改备份已在提交后清理；源码、配图和来源清单保留归档。
