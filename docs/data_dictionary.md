# 整理后数据字典

实际字段与类型见 [prepared_schema.json](../data/manifests/prepared_schema.json)，处理计数见 [数据整理报告](data_preparation.md)。

## 数据表

数据均在服务器 `data/processed/`，不推送至 GitHub。

| 表 | 内容与唯一性 |
| --- | --- |
| `trips_all/*.parquet` | 保留全部行程行；`source_archive + source_member + source_row` 定位原始记录 |
| `trips_clean/*.parquet` | 按已确认规则过滤；冲突 ID 仍可能出现多条记录 |
| `duplicate_ids.parquet` | 同一 ID 体系内出现多次的 ID、原始行数与内容版本数 |
| `weather_observations.parquet` | 两个 NOAA 站点的六类目标观测，保留完整历史；键为站点、日期、变量 |
| `weather_daily.parquet` | 天气宽表，键为 `station_id + weather_date` |
| `station_snapshots.parquet` | 历史站点表与当前 GBFS，分别标注来源，不作为跨年统一站点名录 |
| `station_identity_audit.parquet` | 行程中每个站点 ID 的名称变体数量、观测日期及坐标范围 |
| `station_snapshot_audit.parquet` | 分开核对历史与 GBFS 快照中的名称和坐标范围 |
| `preparation.json` | 本次整理版本、规则、计数和文件校验值 |

## 行程字段

| 字段 | 口径 |
| --- | --- |
| `ride_id` | 原始行程 ID，按字符串保存，保留前导零 |
| `id_namespace` | `legacy`：旧 `trip_id`；`modern`：新版 `ride_id`，两个体系不互相判重 |
| `source_archive`、`source_member`、`source_row` | 来源包、CSV 成员、CSV 数据行序号（从 1 开始，不含表头） |
| `raw_record_sha256` | 原始字段值按原列顺序序列化后的 SHA-256；不包含来源路径、行号及列名 |
| `started_at_raw/ended_at_raw` | 来源时间文本；清除首尾空白，空串置空 |
| `started_at/ended_at` | 解析后的本地墙上时间，`TIMESTAMP`，不附加或转换时区 |
| `trip_date` | `started_at` 的日期，用于日尺度连接 |
| `timestamps_have_seconds` | 起止时间文本均含秒，便于判断取整差异 |
| `reported_duration_raw/seconds` | 旧数据原报时长及解析秒数；支持千位分隔符，无法解析或非有限数值置空 |
| `wall_duration_seconds` | 结束与开始墙上时间之差，单位秒，保留小数 |
| `duration_seconds/source` | 优先使用原报秒数，否则用墙上时间差；来源为 `reported/wall_clock` |
| `rider_type_raw` | 来源用户类型，保留来源标签 |
| `rider_type` | Subscriber/member → member；Customer/casual → casual；Dependent 等其他标签 → unknown，不猜测映射 |
| `rideable_type` | 新版原车型，含 electric_scooter；旧版未提供则置空，不推定为某种车型 |
| `bike_id/gender_raw/birth_year_raw` | 旧数据原字段；新版未提供则置空，不推断性别、年龄 |
| `start/end_station_id/name` | 起终点站点 ID 和名称；字符串去首尾空白，空串置空，不按名称强行合并 ID |
| `start/end_lat/lng` | 来源坐标转数值；旧版未提供则置空，不用当前站点快照回填 |
| `duplicate_id_count/id_variant_count` | 同一 ID 体系中的记录数、原始字段值版本数；唯一 ID 均为 1，缺失 ID 的记录数为 0 |
| `exact_duplicate_rank` | 同 ID、同原始字段值的稳定顺序，按来源包、成员、行号排序；过滤子集保留第 1 条 |

空值保留为空，不用 0、当前站点或其他年份数据填充。原始 CSV 和 ZIP 保持不变。

## 行程质量标记

标记计数可以重叠，不等于逐项移除数。

| 标记 | 含义 |
| --- | --- |
| `flag_id_missing` | ID 为空 |
| `flag_time_invalid` | 任一起止时间缺失或无法解析 |
| `flag_duration_missing/nonpositive/short/long` | 时长缺失、≤0、0–60 秒之间、超过 86400 秒；60 和 86400 秒都保留 |
| `flag_reported_duration_invalid` | 原报时长有文本但无法转为有限数值 |
| `flag_negative_wall_duration` | 墙上时间差小于 0 |
| `flag_duration_mismatch` | 原报与墙上时长差异：秒级时间戳超过 1 秒，分钟级超过 60 秒 |
| `flag_station_missing` | 起终点站点 ID、名称任一缺失 |
| `flag_rider_unmapped` | 用户类型未识别 |
| `flag_coordinate_missing` | 新版来源坐标任一缺失或无法解析；旧版结构性缺失不据此判异常 |
| `flag_coordinate_invalid` | 数值纬度超出 ±90 或经度超出 ±180 |
| `flag_zero_coordinates` | 起点或终点为 (0, 0) |
| `flag_outside_source_period` | 出发日期不在来源包标示的月份范围内 |
| `flag_duplicate_id` | 同一 ID 体系下多条记录 |
| `flag_conflicting_id` | 同一 ID 下有不同原始字段值版本；按用户决定保留并标记 |

过滤规则依次为：ID 完整 → 起止时间可解析 → 时长在 [60, 86400] 秒 → 起终点站四项信息完整 → 去除完全相同的额外副本 → 排除明确标注的 electric_scooter。车型缺失的旧记录保留。其余质量标记不自动触发删行，冲突 ID 保留。过滤子集反映该筛选口径，不代表全市全部出行。

## 天气字段与单位

NOAA 原值与标记定义依据下载时保留的 [GHCN-Daily 文档](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt)。

| 变量 | 原始单位 | 转换 | 宽表字段 |
| --- | --- | --- | --- |
| PRCP | 0.1 mm | ×0.1 | `prcp_mm` |
| SNWD | mm | 不变 | `snwd_mm` |
| SNOW | mm | 不变 | `snow_mm` |
| AWND | 0.1 m/s | ×0.1 | `awnd_m_s` |
| TMAX | 0.1 °C | ×0.1 | `tmax_c` |
| TMIN | 0.1 °C | ×0.1 | `tmin_c` |

长表保留 `raw_value`、`converted_value`、`usable_value`。-9999 对应缺失；质量标记非空或降水、雪、风速出现负值时，可用值置空，原值不删除。`measurement_flag/quality_flag/source_flag/observation_time` 分别记录测量、质量、来源标记和观测时刻；`is_trace` 记录 T 微量标记。

宽表取可用值，另保留 `prcp_trace/snow_trace/snwd_trace` 与 `any_quality_flagged`。微量标记为真时，即使数值为 0，也不能解释为完全没有降水或雪。缺失不补零，不跨站自动填补。主站为 O’Hare `USW00094846`，Midway `USW00014819` 单独保留。

## 时间与站点边界

旧包 README 将行程时间标为 CST，当前 GBFS 的系统时区为 America/Chicago。本阶段保留来源的日期与墙上时间，不推断固定 UTC 偏移；夏令时切换附近的墙上时长可能有歧义。NOAA 按其观测日期标签连接，观测时刻保留在长表，不将其日界线擅自重分配到夏令时日期。

站点表的 `source_scope` 区分 `historical/gbfs_current`；`source_label/source_member` 定位来源。`online_date_raw` 是来源上线或创建时间文本，不当作快照有效时间；历史快照观测时间未知则 `observed_at_utc` 为空，GBFS 则保存其 UTC 更新时间。两个来源的站点 ID 体系不自动对应。

站点名称变体可能来自改名、迁移、别名或 ID 复用。当前审计只标出候选变化，不据此认定为同一物理站点或直接合并。

坐标范围也可能受定位误差、精度变化或无效零坐标影响，不能直接解释为迁移距离。2013 年及 2026 年数据不是完整年度。
