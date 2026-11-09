"""按已确认规则整理全部历史数据，保留全量表并生成过滤子集。"""

import argparse
import calendar
import csv
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import duckdb

from .download_data import MANIFEST, ROOT, STATIONS, sha256, utc_now, write_json
from .verify_data import archive_months

ALIASES = {
    "ride_id": ("ride_id", "trip_id", "01 - Rental Details Rental ID"),
    "started_at_raw": (
        "started_at",
        "starttime",
        "start_time",
        "01 - Rental Details Local Start Time",
    ),
    "ended_at_raw": (
        "ended_at",
        "stoptime",
        "end_time",
        "01 - Rental Details Local End Time",
    ),
    "rider_type_raw": ("member_casual", "usertype", "User Type"),
    "rideable_type": ("rideable_type",),
    "reported_duration_raw": (
        "tripduration",
        "01 - Rental Details Duration In Seconds Uncapped",
    ),
    "bike_id": ("bikeid", "01 - Rental Details Bike ID"),
    "gender_raw": ("gender", "Member Gender"),
    "birth_year_raw": (
        "birthday",
        "birthyear",
        "05 - Member Details Member Birthday Year",
    ),
    "start_station_id": (
        "start_station_id",
        "from_station_id",
        "03 - Rental Start Station ID",
    ),
    "start_station_name": (
        "start_station_name",
        "from_station_name",
        "03 - Rental Start Station Name",
    ),
    "end_station_id": ("end_station_id", "to_station_id", "02 - Rental End Station ID"),
    "end_station_name": (
        "end_station_name",
        "to_station_name",
        "02 - Rental End Station Name",
    ),
    "start_lat_raw": ("start_lat",),
    "start_lng_raw": ("start_lng",),
    "end_lat_raw": ("end_lat",),
    "end_lng_raw": ("end_lng",),
}
WEATHER = {
    "PRCP": ("prcp_mm", 0.1),
    "SNWD": ("snwd_mm", 1.0),
    "SNOW": ("snow_mm", 1.0),
    "AWND": ("awnd_m_s", 0.1),
    "TMAX": ("tmax_c", 0.1),
    "TMIN": ("tmin_c", 0.1),
}
POLICY = {
    "duration_min_seconds": 60,
    "duration_max_seconds": 86400,
    "require_complete_stations": True,
    "exact_duplicates": "keep_first",
    "conflicting_ids": "retain_and_flag",
    "exclude_rideable_types": ["electric_scooter"],
}
BASE_RULES = [
    ("ID 完整", "ride_id IS NOT NULL"),
    ("起止时间可解析", "started_at IS NOT NULL AND ended_at IS NOT NULL"),
    ("时长 60 秒至 24 小时", "duration_seconds BETWEEN 60 AND 86400"),
    ("起终点站信息完整", "NOT flag_station_missing"),
    ("完全相同重复仅保留一条", "exact_duplicate_rank = 1"),
    (
        "排除明确标注的电动滑板车",
        "COALESCE(LOWER(rideable_type) <> 'electric_scooter', TRUE)",
    ),
]


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def text_column(columns, candidates):
    for name in candidates:
        if name in columns:
            return f"NULLIF(TRIM({identifier(name)}), '')"
    return "NULL::VARCHAR"


def timestamp(expression):
    return f"COALESCE(TRY_CAST({expression} AS TIMESTAMP), TRY_STRPTIME({expression}, ['%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M']))"


def number(expression):
    value = f"TRY_CAST(REPLACE({expression}, ',', '') AS DOUBLE)"
    return f"CASE WHEN ISFINITE({value}) THEN {value} END"


def read_csv(connection, path):
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM read_csv(?, all_varchar=true, header=true, delim=',', quote='\"', escape='\"', parallel=false, strict_mode=true, ignore_errors=false)",
        [str(path)],
    )
    return [row[0] for row in connection.execute("DESCRIBE raw").fetchall()]


def copy_parquet(connection, query, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"COPY ({query}) TO {literal(path)} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def normalize_trips(connection, csv_path, source, output):
    columns = read_csv(connection, csv_path)
    mapping = {name: text_column(columns, aliases) for name, aliases in ALIASES.items()}
    if any(
        mapping[name] == "NULL::VARCHAR"
        for name in ["ride_id", "started_at_raw", "ended_at_raw", "rider_type_raw"]
    ):
        raise ValueError(f"缺少必要字段：{source['member']}")
    family = "modern" if "ride_id" in columns else "legacy"
    months = sorted(archive_months(source["archive"]))
    year, month = map(int, months[-1].split("-"))
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    selected = ", ".join(
        f"{expression} AS {name}" for name, expression in mapping.items()
    )
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE normalized AS
        WITH mapped AS (
            SELECT {selected}, {literal(family)} AS id_namespace,
                {literal(source["archive"])} AS source_archive,
                {literal(source["member"])} AS source_member,
                ROW_NUMBER() OVER () AS source_row,
                SHA256(TO_JSON(LIST_VALUE({", ".join(identifier(name) for name in columns)}))) AS raw_record_sha256
            FROM raw r
        ), typed AS (
            SELECT *, {timestamp("started_at_raw")} AS started_at,
                {timestamp("ended_at_raw")} AS ended_at,
                {number("reported_duration_raw")} AS reported_duration_seconds,
                {", ".join(number(name + "_raw") + " AS " + name for name in ["start_lat", "start_lng", "end_lat", "end_lng"])},
                CASE LOWER(rider_type_raw) WHEN 'subscriber' THEN 'member' WHEN 'member' THEN 'member'
                    WHEN 'customer' THEN 'casual' WHEN 'casual' THEN 'casual' ELSE 'unknown' END AS rider_type
            FROM mapped
        ), timed AS (
            SELECT *, EPOCH(ended_at - started_at) AS wall_duration_seconds,
                CAST(started_at AS DATE) AS trip_date,
                REGEXP_MATCHES(COALESCE(started_at_raw,''), '[ :][0-9]{{1,2}}:[0-9]{{2}}:[0-9]{{2}}')
                AND REGEXP_MATCHES(COALESCE(ended_at_raw,''), '[ :][0-9]{{1,2}}:[0-9]{{2}}:[0-9]{{2}}') AS timestamps_have_seconds
            FROM typed
        ), valued AS (
            SELECT *, COALESCE(reported_duration_seconds, wall_duration_seconds) AS duration_seconds,
                CASE WHEN reported_duration_seconds IS NOT NULL THEN 'reported' ELSE 'wall_clock' END AS duration_source
            FROM timed
        )
        SELECT * EXCLUDE(start_lat_raw, start_lng_raw, end_lat_raw, end_lng_raw),
            ride_id IS NULL AS flag_id_missing,
            started_at IS NULL OR ended_at IS NULL AS flag_time_invalid,
            duration_seconds IS NULL AS flag_duration_missing,
            reported_duration_raw IS NOT NULL AND reported_duration_seconds IS NULL AS flag_reported_duration_invalid,
            COALESCE(duration_seconds <= 0, FALSE) AS flag_duration_nonpositive,
            COALESCE(duration_seconds > 0 AND duration_seconds < 60, FALSE) AS flag_duration_short,
            COALESCE(duration_seconds > 86400, FALSE) AS flag_duration_long,
            COALESCE(wall_duration_seconds < 0, FALSE) AS flag_negative_wall_duration,
            COALESCE(ABS(reported_duration_seconds - wall_duration_seconds) > CASE WHEN timestamps_have_seconds THEN 1 ELSE 60 END, FALSE) AS flag_duration_mismatch,
            start_station_id IS NULL OR start_station_name IS NULL OR end_station_id IS NULL OR end_station_name IS NULL AS flag_station_missing,
            rider_type = 'unknown' AS flag_rider_unmapped,
            id_namespace = 'modern' AND (start_lat IS NULL OR start_lng IS NULL OR end_lat IS NULL OR end_lng IS NULL) AS flag_coordinate_missing,
            COALESCE(start_lat NOT BETWEEN -90 AND 90 OR end_lat NOT BETWEEN -90 AND 90 OR start_lng NOT BETWEEN -180 AND 180 OR end_lng NOT BETWEEN -180 AND 180, FALSE) AS flag_coordinate_invalid,
            COALESCE((start_lat = 0 AND start_lng = 0) OR (end_lat = 0 AND end_lng = 0), FALSE) AS flag_zero_coordinates,
            COALESCE(trip_date < DATE {literal(months[0] + "-01")} OR trip_date > DATE {literal(last_day)}, FALSE) AS flag_outside_source_period
        FROM valued
    """)
    raw_rows = connection.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    normalized_rows = connection.execute("SELECT COUNT(*) FROM normalized").fetchone()[
        0
    ]
    if raw_rows != normalized_rows:
        raise ValueError("标准化前后记录数不守恒")
    copy_parquet(connection, "SELECT * FROM normalized", output)
    return {
        "archive": source["archive"],
        "member": source["member"],
        "rows": raw_rows,
        "schema_family": family,
        "column_mapping": {
            name: next((column for column in aliases if column in columns), None)
            for name, aliases in ALIASES.items()
        },
    }


def prepare_weather(connection, output):
    connection.execute(
        "CREATE TEMP TABLE weather_raw (station_id VARCHAR, day_raw VARCHAR, element VARCHAR, value_raw VARCHAR, mflag VARCHAR, qflag VARCHAR, sflag VARCHAR, obs_time VARCHAR, source_file VARCHAR)"
    )
    source_rows = {}
    for station in STATIONS:
        path = ROOT / f"data/raw/noaa/{station}.csv.gz"
        relation = connection.read_csv(
            str(path),
            header=False,
            columns={
                name: "VARCHAR"
                for name in [
                    "station_id",
                    "day_raw",
                    "element",
                    "value_raw",
                    "mflag",
                    "qflag",
                    "sflag",
                    "obs_time",
                ]
            },
            parallel=False,
            strict_mode=True,
        )
        relation.create_view("weather_source", replace=True)
        source_rows[station] = connection.execute(
            "SELECT COUNT(*) FROM weather_source"
        ).fetchone()[0]
        connection.execute(
            f"INSERT INTO weather_raw SELECT *, {literal(path.name)} FROM weather_source"
        )
    element_list = ",".join(literal(name) for name in WEATHER)
    scales = (
        "CASE element "
        + " ".join(
            f"WHEN {literal(name)} THEN {scale}" for name, (_, scale) in WEATHER.items()
        )
        + " END"
    )
    connection.execute(f"""
        CREATE TEMP TABLE weather_long AS
        WITH parsed AS (
            SELECT station_id, CAST(STRPTIME(day_raw, '%Y%m%d') AS DATE) AS weather_date,
                element, CAST(value_raw AS INTEGER) AS raw_value,
                NULLIF(TRIM(mflag),'') AS measurement_flag, NULLIF(TRIM(qflag),'') AS quality_flag,
                NULLIF(TRIM(sflag),'') AS source_flag, obs_time AS observation_time, source_file
            FROM weather_raw WHERE element IN ({element_list})
        ), converted AS (
            SELECT *, raw_value = -9999 AS is_missing,
                quality_flag IS NOT NULL AS is_quality_flagged,
                COALESCE(measurement_flag = 'T', FALSE) AS is_trace,
                CASE WHEN raw_value != -9999 THEN CAST(raw_value * ({scales}) AS DOUBLE) END AS converted_value
            FROM parsed
        )
        SELECT *, COALESCE(element IN ('PRCP','SNWD','SNOW','AWND') AND converted_value < 0, FALSE) AS is_physically_invalid,
            CASE WHEN NOT is_missing AND NOT is_quality_flagged AND NOT (element IN ('PRCP','SNWD','SNOW','AWND') AND converted_value < 0) THEN converted_value END AS usable_value
        FROM converted
    """)
    if connection.execute(
        "SELECT COUNT(*) FROM (SELECT station_id,weather_date,element FROM weather_long GROUP BY ALL HAVING COUNT(*) > 1)"
    ).fetchone()[0]:
        raise ValueError("天气存在重复观测键，需要先确认处理规则")
    wide = ", ".join(
        f"MAX(usable_value) FILTER (WHERE element={literal(element)}) AS {name}"
        for element, (name, _) in WEATHER.items()
    )
    connection.execute(
        f"CREATE TEMP TABLE weather_daily AS SELECT station_id,weather_date,{wide}, BOOL_OR(is_trace) FILTER(WHERE element='PRCP') AS prcp_trace, BOOL_OR(is_trace) FILTER(WHERE element='SNOW') AS snow_trace, BOOL_OR(is_trace) FILTER(WHERE element='SNWD') AS snwd_trace, BOOL_OR(is_quality_flagged) AS any_quality_flagged FROM weather_long GROUP BY station_id,weather_date"
    )
    copy_parquet(
        connection,
        "SELECT * FROM weather_long ORDER BY station_id,weather_date,element",
        output / "weather_observations.parquet",
    )
    copy_parquet(
        connection,
        "SELECT * FROM weather_daily ORDER BY station_id,weather_date",
        output / "weather_daily.parquet",
    )
    return {
        "source_rows": source_rows,
        "target_observations": connection.execute(
            "SELECT COUNT(*) FROM weather_long"
        ).fetchone()[0],
        "daily_rows": connection.execute(
            "SELECT COUNT(*) FROM weather_daily"
        ).fetchone()[0],
        "quality_flagged": connection.execute(
            "SELECT COUNT(*) FROM weather_long WHERE is_quality_flagged"
        ).fetchone()[0],
    }


def prepare_stations(connection, sources, output, temporary):
    connection.execute(
        "CREATE TEMP TABLE stations (station_id VARCHAR, station_name VARCHAR, latitude DOUBLE, longitude DOUBLE, capacity DOUBLE, online_date_raw VARCHAR, source_scope VARCHAR, source_label VARCHAR, source_member VARCHAR, observed_at_utc VARCHAR)"
    )
    for source in sources:
        with (
            ZipFile(ROOT / "data/raw/divvy/trips" / source["archive"]) as archive,
            archive.open(source["member"]) as stream,
            temporary.open("wb") as target,
        ):
            shutil.copyfileobj(stream, target)
        columns = read_csv(connection, temporary)
        expressions = [
            text_column(columns, names)
            for names in [
                ("id",),
                ("name",),
                ("latitude",),
                ("longitude",),
                ("dpcapacity",),
                ("online date", "dateCreated", "online_date"),
            ]
        ]
        connection.execute(
            f"INSERT INTO stations SELECT {expressions[0]}, {expressions[1]}, {number(expressions[2])}, {number(expressions[3])}, {number(expressions[4])}, {expressions[5]}, 'historical', {literal(source['archive'])}, {literal(source['member'])}, NULL FROM raw"
        )
    gbfs = json.loads(
        (ROOT / "data/raw/divvy/stations/station_information.json").read_text()
    )
    observed = datetime.fromtimestamp(gbfs["last_updated"], timezone.utc).isoformat()
    connection.executemany(
        "INSERT INTO stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                str(station["station_id"]),
                station["name"],
                station.get("lat"),
                station.get("lon"),
                station.get("capacity"),
                None,
                "gbfs_current",
                "station_information.json",
                None,
                observed,
            )
            for station in gbfs["data"]["stations"]
        ],
    )
    copy_parquet(
        connection, "SELECT * FROM stations", output / "station_snapshots.parquet"
    )
    return {
        "rows": connection.execute("SELECT COUNT(*) FROM stations").fetchone()[0],
        "historical_tables": len(sources),
        "gbfs_rows": len(gbfs["data"]["stations"]),
    }


def filter_part(connection, path, output, all_rows):
    condition = " AND ".join(f"({rule})" for _, rule in BASE_RULES)
    copy_parquet(
        connection,
        "SELECT * FROM current_trips WHERE " + condition,
        output / "trips_clean" / path.name,
    )
    source = connection.execute(
        "SELECT source_archive,source_member FROM current_trips LIMIT 1"
    ).fetchone() or ("", "")
    row = {
        "part": path.name,
        "archive": source[0],
        "member": source[1],
        "rows_all": all_rows,
    }
    predicates = []
    for stage, (_, rule) in enumerate(BASE_RULES, 1):
        predicates.append(f"({rule})")
        row[f"rows_after_rule_{stage}"] = connection.execute(
            "SELECT COUNT(*) FROM current_trips WHERE " + " AND ".join(predicates)
        ).fetchone()[0]
    return row


def refilter_trips(connection, output):
    per_file = []
    files = sorted((output / "trips_all").glob("*.parquet"))
    for index, path in enumerate(files, 1):
        connection.execute(
            f"CREATE OR REPLACE TEMP VIEW current_trips AS SELECT * FROM read_parquet({literal(path)})"
        )
        rows = connection.execute("SELECT COUNT(*) FROM current_trips").fetchone()[0]
        per_file.append(filter_part(connection, path, output, rows))
        if index % 10 == 0 or index == len(files):
            print(f"重新过滤：{index}/{len(files)} 个来源文件", flush=True)
    return per_file


def enrich_trips(connection, intermediate, output):
    connection.execute(
        f"CREATE TEMP VIEW base_trips AS SELECT * FROM read_parquet({literal(intermediate / '*.parquet')})"
    )
    connection.execute(
        "CREATE TEMP TABLE duplicate_keys AS SELECT id_namespace,ride_id,COUNT(*) AS id_count FROM base_trips WHERE ride_id IS NOT NULL GROUP BY id_namespace,ride_id HAVING COUNT(*)>1"
    )
    connection.execute("""CREATE TEMP TABLE duplicate_rows AS SELECT b.id_namespace,b.ride_id,b.source_archive,b.source_member,b.source_row,
        d.id_count, COUNT(DISTINCT b.raw_record_sha256) OVER(PARTITION BY b.id_namespace,b.ride_id) AS id_variant_count,
        ROW_NUMBER() OVER(PARTITION BY b.id_namespace,b.ride_id,b.raw_record_sha256 ORDER BY b.source_archive,b.source_member,b.source_row) AS exact_duplicate_rank
        FROM base_trips b JOIN duplicate_keys d USING(id_namespace,ride_id)""")
    copy_parquet(
        connection,
        "SELECT id_namespace,ride_id,MAX(id_count) AS id_count,MAX(id_variant_count) AS id_variant_count FROM duplicate_rows GROUP BY id_namespace,ride_id",
        output / "duplicate_ids.parquet",
    )
    per_file = []
    for index, path in enumerate(sorted(intermediate.glob("*.parquet")), 1):
        connection.execute(f"""CREATE OR REPLACE TEMP TABLE current_trips AS SELECT b.*,
            COALESCE(d.id_count, CASE WHEN b.ride_id IS NULL THEN 0 ELSE 1 END) AS duplicate_id_count,
            COALESCE(d.id_variant_count,1) AS id_variant_count, COALESCE(d.exact_duplicate_rank,1) AS exact_duplicate_rank,
            COALESCE(d.id_count>1,FALSE) AS flag_duplicate_id, COALESCE(d.id_variant_count>1,FALSE) AS flag_conflicting_id
            FROM read_parquet({literal(path)}) b LEFT JOIN duplicate_rows d USING(source_archive,source_member,source_row)""")
        all_rows = connection.execute("SELECT COUNT(*) FROM current_trips").fetchone()[
            0
        ]
        if (
            all_rows
            != connection.execute(
                f"SELECT COUNT(*) FROM read_parquet({literal(path)})"
            ).fetchone()[0]
        ):
            raise ValueError("重复标记连接改变了记录数")
        copy_parquet(
            connection, "SELECT * FROM current_trips", output / "trips_all" / path.name
        )
        per_file.append(filter_part(connection, path, output, all_rows))
        if index % 10 == 0 or index == len(list(intermediate.glob("*.parquet"))):
            print(f"生成全量表与过滤子集：{index} 个来源文件", flush=True)
    return per_file


def summarize(connection, output, source_rows, per_file):
    connection.execute(
        f"CREATE TEMP VIEW trips_all AS SELECT * FROM read_parquet({literal(output / 'trips_all/*.parquet')})"
    )
    connection.execute(
        f"CREATE TEMP VIEW trips_clean AS SELECT * FROM read_parquet({literal(output / 'trips_clean/*.parquet')})"
    )
    total, first, last = connection.execute(
        "SELECT COUNT(*),MIN(trip_date),MAX(trip_date) FROM trips_all"
    ).fetchone()
    clean = connection.execute("SELECT COUNT(*) FROM trips_clean").fetchone()[0]
    if total != sum(source["rows"] for source in source_rows) or clean != sum(
        row[f"rows_after_rule_{len(BASE_RULES)}"] for row in per_file
    ):
        raise ValueError("全量数据或过滤结果记录数不守恒")
    flags = [
        row[0]
        for row in connection.execute("DESCRIBE trips_all").fetchall()
        if row[0].startswith("flag_")
    ]
    counts = connection.execute(
        "SELECT "
        + ",".join(f"COUNT(*) FILTER(WHERE {flag})" for flag in flags)
        + " FROM trips_all"
    ).fetchone()
    rule_counts = [total] + [
        sum(row[f"rows_after_rule_{stage}"] for row in per_file)
        for stage in range(1, len(BASE_RULES) + 1)
    ]
    rule_audit = [
        {
            "rule": name,
            "before": rule_counts[index],
            "after": rule_counts[index + 1],
            "removed": rule_counts[index] - rule_counts[index + 1],
        }
        for index, (name, _) in enumerate(BASE_RULES)
    ]
    yearly = connection.execute(
        "SELECT YEAR(trip_date) AS year,COUNT(*) AS rows_all FROM trips_all GROUP BY year ORDER BY year"
    ).fetchall()
    clean_years = dict(
        connection.execute(
            "SELECT YEAR(trip_date),COUNT(*) FROM trips_clean GROUP BY 1"
        ).fetchall()
    )
    unmatched = connection.execute(
        "SELECT COUNT(*) FROM trips_clean t LEFT JOIN weather_daily w ON w.station_id='USW00094846' AND w.weather_date=t.trip_date WHERE w.station_id IS NULL"
    ).fetchone()[0]
    copy_parquet(
        connection,
        """WITH observations AS (
        SELECT start_station_id AS station_id,start_station_name AS station_name,trip_date,start_lat AS latitude,start_lng AS longitude FROM trips_all
        UNION ALL SELECT end_station_id,end_station_name,trip_date,end_lat,end_lng FROM trips_all)
        SELECT station_id, COUNT(DISTINCT station_name) AS name_variants, MIN(trip_date) AS first_seen_date,MAX(trip_date) AS last_seen_date,
            MIN(latitude) AS latitude_min,MAX(latitude) AS latitude_max,MIN(longitude) AS longitude_min,MAX(longitude) AS longitude_max
        FROM observations WHERE station_id IS NOT NULL GROUP BY station_id""",
        output / "station_identity_audit.parquet",
    )
    copy_parquet(
        connection,
        f"SELECT source_scope,station_id,COUNT(*) AS observations,COUNT(DISTINCT station_name) AS name_variants,MIN(latitude) AS latitude_min,MAX(latitude) AS latitude_max,MIN(longitude) AS longitude_min,MAX(longitude) AS longitude_max FROM read_parquet({literal(output / 'station_snapshots.parquet')}) GROUP BY source_scope,station_id",
        output / "station_snapshot_audit.parquet",
    )
    return {
        "rows_all": total,
        "rows_clean": clean,
        "rows_removed": total - clean,
        "first_date": str(first),
        "last_date": str(last),
        "quality_flags": dict(zip(flags, counts)),
        "filter_audit": rule_audit,
        "yearly_rows": [
            {"year": year, "rows_all": count, "rows_clean": clean_years.get(year, 0)}
            for year, count in yearly
        ],
        "weather_unmatched_clean_rows": unmatched,
        "rider_categories": [
            {"raw": raw, "normalized": normalized, "rows": count}
            for raw, normalized, count in connection.execute(
                "SELECT rider_type_raw,rider_type,COUNT(*) FROM trips_all GROUP BY ALL ORDER BY 3 DESC"
            ).fetchall()
        ],
        "vehicle_categories": [
            {"type": name, "rows_all": count, "rows_clean": kept}
            for name, count, kept in connection.execute(
                "WITH a AS (SELECT rideable_type,COUNT(*) AS rows_all FROM trips_all GROUP BY 1), b AS (SELECT rideable_type,COUNT(*) AS rows_clean FROM trips_clean GROUP BY 1) SELECT a.rideable_type,a.rows_all,COALESCE(b.rows_clean,0) FROM a LEFT JOIN b ON a.rideable_type IS NOT DISTINCT FROM b.rideable_type ORDER BY a.rows_all DESC"
            ).fetchall()
        ],
        "station_ids_with_multiple_names": connection.execute(
            f"SELECT COUNT(*) FROM read_parquet({literal(output / 'station_identity_audit.parquet')}) WHERE name_variants>1"
        ).fetchone()[0],
    }


def report(summary):
    trips = summary["trips"]
    lines = [
        "# 数据整理",
        "",
        f"生成时间（UTC）：{summary['prepared_at']}。按已确认规则执行；全量标准化数据保留全部原始行。",
        "",
        f"- 处理 {len(summary['sources'])} 个行程 CSV，共 {trips['rows_all']:,} 行；过滤后 {trips['rows_clean']:,} 行，移除 {trips['rows_removed']:,} 行。",
        f"- 完整扫描的行程日期：{trips['first_date']} 至 {trips['last_date']}。",
        f"- 过滤子集与 O’Hare 天气按来源日期连接，未匹配记录 {trips['weather_unmatched_clean_rows']:,} 行。",
        "",
        "## 过滤记录",
        "",
        "以下规则依次应用，移除数可以相加；质量标记计数可能重叠。",
        "",
        "| 规则 | 处理前 | 保留 | 移除 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {item['rule']} | {item['before']:,} | {item['after']:,} | {item['removed']:,} |"
        for item in trips["filter_audit"]
    ]
    lines += [
        "",
        "同 ID 不同原始内容不因冲突删行，仍需满足其他过滤规则；只移除字段值完全相同的额外副本。旧版与新版 ID 分属两个命名空间。",
        "",
        "## 年度记录数",
        "",
        "| 年份 | 全量 | 过滤后 |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {item['year']} | {item['rows_all']:,} | {item['rows_clean']:,} |"
        for item in trips["yearly_rows"]
    ]
    lines += ["", "## 质量标记", "", "| 标记 | 记录数 |", "| --- | --- |"]
    lines += [
        f"| `{name}` | {count:,} |" for name, count in trips["quality_flags"].items()
    ]
    lines += [
        "",
        "## 车型与用户标签",
        "",
        "| 原车型 | 全量 | 过滤后 |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {item['type'] or '未提供'} | {item['rows_all']:,} | {item['rows_clean']:,} |"
        for item in trips["vehicle_categories"]
    ]
    lines += [
        "",
        "用户标签映射："
        + "；".join(
            f"{item['raw']} → {item['normalized']}（{item['rows']:,} 行）"
            for item in trips["rider_categories"]
        )
        + "。",
    ]
    lines += [
        "",
        "## 口径与限制",
        "",
        "- 过滤子集要求起终点 ID、名称完整，其计数不代表全市全部行程；全量表可用于检查筛选偏差。",
        "- 按用户确认，分析子集排除明确标注的 electric_scooter；旧版未提供车型的记录保留。",
        "- 2013 年与 2026 年为不完整年度，不直接比较其全年总量。月度包也可能包含前月开始的行程，统一按出发日期归类。",
        "- 旧数据优先使用原报秒数；保留时间戳差值，分钟精度时允许 60 秒内差异，不将该取整差异判为时长冲突。",
        "- 行程时间保留来源的本地墙上时间，不擅自转换 UTC；旧 README 标为 CST，GBFS 系统时区为 America/Chicago。夏令时切换附近的墙上时长仍可能存在歧义。",
        "- 天气按原始日期标签连接，保留观测时刻与质量标记；不将 NOAA 的观测日重新解释为当地夏令时日。",
        "- 当前 GBFS 与历史站点分别标明来源；不将当前快照回填旧行程。同一 ID 的多名称只提示变更或别名，不自动合并。",
        "- 天气原值、单位转换值与可用值分别保存，质量失败值不用于宽表；微量降水/雪的 T 标记保留，不将缺失补零。",
        "",
        "详细字段见 [数据字典](data_dictionary.md)，来源与处理计数见 [preparation.json](../data/manifests/preparation.json)。大表保存在服务器 `data/processed/`。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--filter-only",
        action="store_true",
        help="验证并复用已整理表，只重做过滤和审计",
    )
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    if not manifest.get("complete") or manifest.get("failures"):
        raise ValueError("下载尚未完成")
    for record in manifest["files"]:
        path = ROOT / record["path"]
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"原始文件与清单不符：{record['path']}")
    with (ROOT / "data/manifests/zip_contents.csv").open(newline="") as stream:
        contents = list(csv.DictReader(stream))
    trip_sources = [row for row in contents if row["kind"] == "trips"]
    station_sources = [row for row in contents if row["kind"] == "stations"]
    with TemporaryDirectory(prefix=".prepare-", dir=ROOT / "data") as directory:
        stage = Path(directory)
        work = stage / ".work"
        work.mkdir()
        intermediate = work / "normalized"
        intermediate.mkdir()
        connection = duckdb.connect(
            config={
                "threads": 4,
                "memory_limit": "4GB",
                "temp_directory": str(work / "spill"),
            }
        )
        previous = None
        if args.filter_only:
            existing = ROOT / "data/processed"
            previous = json.loads((existing / "preparation.json").read_text())
            if previous["download_manifest_sha256"] != sha256(MANIFEST):
                raise ValueError("原始下载版本已变化，请完整执行整理")
            for record in previous["outputs"]:
                path = existing / record["path"]
                if (
                    path.stat().st_size != record["bytes"]
                    or sha256(path) != record["sha256"]
                ):
                    raise ValueError(f"已整理文件与清单不符：{record['path']}")
            for path in existing.iterdir():
                if path.name not in {"trips_clean", "preparation.json"}:
                    if path.is_dir():
                        shutil.copytree(path, stage / path.name)
                    else:
                        shutil.copy2(path, stage / path.name)
            sources, weather, stations = (
                previous["sources"],
                previous["weather"],
                previous["stations"],
            )
            connection.execute(
                f"CREATE TEMP VIEW weather_daily AS SELECT * FROM read_parquet({literal(stage / 'weather_daily.parquet')})"
            )
            per_file = refilter_trips(connection, stage)
        else:
            sources = []
            for index, source in enumerate(trip_sources, 1):
                temporary = work / "source.csv"
                with (
                    ZipFile(
                        ROOT / "data/raw/divvy/trips" / source["archive"]
                    ) as archive,
                    archive.open(source["member"]) as stream,
                    temporary.open("wb") as target,
                ):
                    shutil.copyfileobj(stream, target)
                sources.append(
                    normalize_trips(
                        connection,
                        temporary,
                        source,
                        intermediate / f"part-{index:03d}.parquet",
                    )
                )
                if index % 10 == 0 or index == len(trip_sources):
                    print(
                        f"标准化行程：{index}/{len(trip_sources)}，累计 {sum(s['rows'] for s in sources):,} 行",
                        flush=True,
                    )
            weather = prepare_weather(connection, stage)
            stations = prepare_stations(
                connection, station_sources, stage, work / "source.csv"
            )
            per_file = enrich_trips(connection, intermediate, stage)
        trips = summarize(connection, stage, sources, per_file)
        schema = {
            name: [
                {"name": row[0], "type": row[1]}
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM read_parquet({literal(pattern)})"
                ).fetchall()
            ]
            for name, pattern in {
                "trips": stage / "trips_all/*.parquet",
                "weather_observations": stage / "weather_observations.parquet",
                "weather_daily": stage / "weather_daily.parquet",
                "station_snapshots": stage / "station_snapshots.parquet",
                "station_identity_audit": stage / "station_identity_audit.parquet",
                "station_snapshot_audit": stage / "station_snapshot_audit.parquet",
                "duplicate_ids": stage / "duplicate_ids.parquet",
            }.items()
        }
        connection.close()
        shutil.rmtree(work)
        summary = {
            "prepared_at": utc_now(),
            "duckdb_version": duckdb.__version__,
            "build_mode": "filter_only" if args.filter_only else "full",
            "normalized_at": previous.get("normalized_at", previous["prepared_at"])
            if previous
            else utc_now(),
            "download_manifest_sha256": sha256(MANIFEST),
            "policy": POLICY,
            "trips": trips,
            "weather": weather,
            "stations": stations,
            "sources": sources,
            "outputs": [
                {
                    "path": str(path.relative_to(stage)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
                for path in sorted(stage.rglob("*.parquet"))
            ],
        }
        write_json(stage / "preparation.json", summary)
        destination = ROOT / "data/processed"
        backup = stage.parent / (stage.name + "-previous")
        try:
            if destination.exists():
                destination.rename(backup)
            stage.rename(destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        write_json(ROOT / "data/manifests/preparation.json", summary)
        write_json(ROOT / "data/manifests/prepared_schema.json", schema)
        with (ROOT / "data/manifests/preparation_by_file.csv").open(
            "w", newline=""
        ) as stream:
            writer = csv.DictWriter(
                stream, fieldnames=list(per_file[0]), lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(per_file)
        (ROOT / "docs/data_preparation.md").write_text(report(summary))
    print(
        f"整理完成：全量 {trips['rows_all']:,} 行，过滤后 {trips['rows_clean']:,} 行。",
        flush=True,
    )


if __name__ == "__main__":
    main()
