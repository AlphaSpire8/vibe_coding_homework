"""在已冻结范围内生成探索统计，不分析留出年的关系。"""

import csv
import json
import shutil
from datetime import date
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import numpy as np

from .download_data import ROOT, sha256, utc_now, write_json
from .prepare_data import BASE_RULES, literal

RIDERS = ("member", "casual")
CONFIG = ROOT / "configs/exploration.json"


def query_rows(connection, query):
    result = connection.execute(query)
    names = [column[0] for column in result.description]
    return [dict(zip(names, row)) for row in result.fetchall()]


def export_table(path, rows):
    if not rows:
        raise ValueError(f"统计表为空：{path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def correlation(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return {"n_days": len(x), "pearson": None, "spearman": None}

    def ranks(values):
        _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
        average = np.cumsum(counts) - (counts + 1) / 2
        return average[inverse]

    return {
        "n_days": len(x),
        "pearson": float(np.corrcoef(x, y)[0, 1]),
        "spearman": float(np.corrcoef(ranks(x), ranks(y))[0, 1]),
    }


def build_daily_panel(connection, year, wet_threshold):
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    connection.execute(f"""
        CREATE TEMP TABLE daily AS
        WITH calendar AS (
            SELECT CAST(day AS DATE) AS trip_date FROM GENERATE_SERIES(DATE {literal(start)}, DATE {literal(end)}-1, INTERVAL 1 DAY) t(day)
        ), grouped AS (
            SELECT trip_date,rider_type,COUNT(*) AS rides,MEDIAN(duration_seconds)/60 AS median_duration_min
            FROM selected GROUP BY trip_date,rider_type
        ), grid AS (
            SELECT c.trip_date,r.rider_type, COALESCE(a.raw_rows,0)>0 AS source_observed,
                CASE WHEN COALESCE(a.raw_rows,0)>0 THEN COALESCE(g.rides,0) END AS rides,
                g.median_duration_min
            FROM calendar c CROSS JOIN (VALUES ('member'),('casual')) r(rider_type)
            LEFT JOIN raw_days a USING(trip_date)
            LEFT JOIN grouped g ON g.trip_date=c.trip_date AND g.rider_type=r.rider_type
        ), joined AS (
            SELECT g.*,MONTH(trip_date) AS month,ISODOW(trip_date)>=6 AS is_weekend,
                w.prcp_mm,w.snow_mm,w.snwd_mm,w.awnd_m_s,w.tmax_c,w.tmin_c,w.prcp_trace,
                (w.tmax_c+w.tmin_c)/2 AS temp_mid_c,
                CASE WHEN w.prcp_mm IS NULL THEN NULL
                    WHEN w.prcp_mm=0 AND NOT COALESCE(w.prcp_trace,FALSE) THEN 0
                    WHEN w.prcp_mm<{wet_threshold} THEN 1
                    WHEN w.prcp_mm<5 THEN 2 WHEN w.prcp_mm<10 THEN 3 ELSE 4 END AS rain_group
            FROM grid g LEFT JOIN weather w ON g.trip_date=w.weather_date
        )
        SELECT *,100.0*rides/NULLIF(AVG(rides) OVER(PARTITION BY rider_type),0) AS annual_index,
            100.0*rides/NULLIF(AVG(rides) OVER(PARTITION BY rider_type,month,is_weekend),0) AS calendar_index
        FROM joined
    """)


def compute_tables(connection, scope, prep):
    year = scope["exploration_year"]
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    predicate = f"trip_date>=DATE {literal(start)} AND trip_date<DATE {literal(end)}"
    source = ROOT / "data/processed"
    connection.execute(
        f"CREATE TEMP VIEW all_scope AS SELECT * FROM read_parquet({literal(source / 'trips_all/*.parquet')}) WHERE {predicate}"
    )
    connection.execute(
        f"CREATE TEMP TABLE selected AS SELECT rider_type,rideable_type,trip_date,started_at,duration_seconds,start_station_id,start_station_name,end_station_id,end_station_name,start_lat,start_lng,flag_conflicting_id FROM read_parquet({literal(source / 'trips_clean/*.parquet')}) WHERE {predicate}"
    )
    connection.execute(
        "CREATE TEMP TABLE raw_days AS SELECT trip_date,COUNT(*) AS raw_rows FROM all_scope GROUP BY trip_date"
    )
    connection.execute(
        f"CREATE TEMP VIEW weather AS SELECT * FROM read_parquet({literal(source / 'weather_daily.parquet')}) WHERE station_id='USW00094846' AND weather_date>=DATE {literal(start)} AND weather_date<DATE {literal(end)}"
    )
    build_daily_panel(connection, year, scope["wet_threshold_mm"])
    tables = {
        "daily": query_rows(
            connection, "SELECT * FROM daily ORDER BY trip_date,rider_type"
        )
    }
    tables["coverage_history"] = [
        {
            **row,
            "retention": row["rows_clean"] / row["rows_all"],
            "role": "existing_quality_counts_only",
        }
        for row in prep["trips"]["yearly_rows"]
    ]
    other_rules = " AND ".join(
        f"({rule})" for _, rule in BASE_RULES if rule != "NOT flag_station_missing"
    )
    groups = []
    for category, column in [("rider", "rider_type"), ("vehicle", "rideable_type")]:
        groups += query_rows(
            connection,
            f"SELECT {literal(category)} AS category,COALESCE({column},'not_reported') AS label,COUNT(*) AS raw_rows,COUNT(*) FILTER(WHERE {other_rules}) AS other_eligible_rows,COUNT(*) FILTER(WHERE ({other_rules}) AND NOT flag_station_missing) AS complete_station_rows FROM all_scope GROUP BY {column} ORDER BY raw_rows DESC",
        )
    tables["selection_quality"] = [
        {
            **row,
            "station_completeness": row["complete_station_rows"]
            / row["other_eligible_rows"]
            if row["other_eligible_rows"]
            else None,
        }
        for row in groups
    ]
    tables["rider_summary"] = query_rows(
        connection,
        "SELECT rider_type,COUNT(*) AS rides,MEDIAN(duration_seconds)/60 AS median_min,QUANTILE_CONT(duration_seconds,0.25)/60 AS q25_min,QUANTILE_CONT(duration_seconds,0.75)/60 AS q75_min,QUANTILE_CONT(duration_seconds,0.95)/60 AS q95_min,AVG(CASE WHEN start_station_id=end_station_id THEN 1.0 ELSE 0 END) AS same_station_share,COUNT(*) FILTER(WHERE flag_conflicting_id) AS conflicting_rows FROM selected GROUP BY rider_type ORDER BY rider_type",
    )
    tables["duration_cdf"] = query_rows(
        connection,
        "WITH bins AS (SELECT rider_type,CAST(CEIL(duration_seconds/60) AS INTEGER) AS minute_upper,COUNT(*) AS n FROM selected GROUP BY rider_type,minute_upper) SELECT *,1.0*SUM(n) OVER(PARTITION BY rider_type ORDER BY minute_upper)/SUM(n) OVER(PARTITION BY rider_type) AS cumulative_share FROM bins ORDER BY rider_type,minute_upper",
    )
    tables["monthly"] = query_rows(
        connection,
        "SELECT month,rider_type,COUNT(rides) AS observed_days,SUM(rides) AS rides,AVG(rides) AS mean_daily_rides FROM daily GROUP BY month,rider_type ORDER BY month,rider_type",
    )
    tables["day_type"] = query_rows(
        connection,
        "SELECT rider_type,is_weekend,COUNT(rides) AS observed_days,AVG(rides) AS mean_daily_rides,MEDIAN(median_duration_min) AS median_daily_duration_min FROM daily GROUP BY rider_type,is_weekend ORDER BY rider_type,is_weekend",
    )
    tables["hourly"] = query_rows(
        connection,
        "WITH a AS (SELECT rider_type,ISODOW(trip_date)>=6 AS is_weekend,HOUR(started_at) AS hour,COUNT(*) AS rides FROM selected GROUP BY ALL) SELECT *,1.0*rides/SUM(rides) OVER(PARTITION BY rider_type,is_weekend) AS share FROM a ORDER BY rider_type,is_weekend,hour",
    )
    tables["monthly_weekend"] = query_rows(
        connection,
        "SELECT month,rider_type,AVG(rides) FILTER(WHERE is_weekend)/NULLIF(AVG(rides) FILTER(WHERE NOT is_weekend),0) AS weekend_weekday_ratio FROM daily GROUP BY month,rider_type ORDER BY month,rider_type",
    )
    tables["stations"] = query_rows(
        connection,
        """WITH counts AS (
        SELECT start_station_id, start_station_name,COUNT(*) AS n FROM selected GROUP BY ALL
        ), names AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY start_station_id ORDER BY n DESC,start_station_name) AS rank FROM counts),
        location AS (SELECT start_station_id,COUNT(*) AS rides,COUNT(DISTINCT start_station_name) AS name_variants,
            AVG(CASE WHEN rider_type='casual' THEN 1.0 ELSE 0 END) AS casual_share,
            MEDIAN(CASE WHEN start_lat BETWEEN -90 AND 90 AND start_lng BETWEEN -180 AND 180 AND NOT(start_lat=0 AND start_lng=0) THEN start_lat END) AS latitude,
            MEDIAN(CASE WHEN start_lat BETWEEN -90 AND 90 AND start_lng BETWEEN -180 AND 180 AND NOT(start_lat=0 AND start_lng=0) THEN start_lng END) AS longitude
        FROM selected GROUP BY start_station_id)
        SELECT l.*,n.start_station_name AS display_name FROM location l JOIN names n ON l.start_station_id=n.start_station_id AND n.rank=1 ORDER BY rides DESC,l.start_station_id""",
    )
    tables["rain"] = query_rows(
        connection,
        "SELECT rider_type,rain_group,COUNT(rides) AS n_days,AVG(rides) AS mean_rides,AVG(calendar_index) AS mean_calendar_index FROM daily WHERE source_observed AND rain_group IS NOT NULL GROUP BY rider_type,rain_group ORDER BY rider_type,rain_group",
    )
    width = scope["temperature_bin_width_c"]
    tables["temperature"] = query_rows(
        connection,
        f"SELECT rider_type,FLOOR(temp_mid_c/{width})*{width} AS bin_left_c,COUNT(rides) AS n_days,AVG(annual_index) AS mean_annual_index,AVG(calendar_index) AS mean_calendar_index FROM daily WHERE source_observed AND temp_mid_c IS NOT NULL GROUP BY rider_type,bin_left_c ORDER BY rider_type,bin_left_c",
    )
    coefficients = []
    for rider in RIDERS:
        days = [row for row in tables["daily"] if row["rider_type"] == rider]
        for outcome in ["rides", "calendar_index"]:
            for feature in ["temp_mid_c", "prcp_mm", "awnd_m_s", "snow_mm"]:
                coefficients.append(
                    {
                        "rider_type": rider,
                        "outcome": outcome,
                        "feature": feature,
                        **correlation(
                            [row[feature] for row in days],
                            [row[outcome] for row in days],
                        ),
                    }
                )
    tables["correlations"] = coefficients
    tables["low_index_days"] = query_rows(
        connection,
        "SELECT trip_date,rider_type,rides,calendar_index,temp_mid_c,prcp_mm,snow_mm FROM daily WHERE source_observed QUALIFY ROW_NUMBER() OVER(PARTITION BY rider_type ORDER BY calendar_index,trip_date)<=5 ORDER BY rider_type,calendar_index",
    )
    return tables


def summarize(tables, scope):
    days = tables["daily"]
    summary = {
        "exploration_year": scope["exploration_year"],
        "holdout_year": scope["holdout_year"],
        "holdout_relationships_analyzed": False,
        "rides": sum(row["rides"] for row in tables["rider_summary"]),
        "riders": {},
        "source_unobserved_days": len(
            {row["trip_date"] for row in days if not row["source_observed"]}
        ),
        "weather_missing_days": len(
            {
                row["trip_date"]
                for row in days
                if any(
                    row[name] is None
                    for name in ["prcp_mm", "temp_mid_c", "awnd_m_s", "snow_mm"]
                )
            }
        ),
    }
    for rider in RIDERS:
        description = next(
            row for row in tables["rider_summary"] if row["rider_type"] == rider
        )
        type_days = [row for row in tables["day_type"] if row["rider_type"] == rider]
        weekday = next(row for row in type_days if not row["is_weekend"])
        weekend = next(row for row in type_days if row["is_weekend"])
        rainfall = [
            row
            for row in days
            if row["rider_type"] == rider
            and row["source_observed"]
            and row["rain_group"] is not None
        ]
        dry = [row for row in rainfall if row["rain_group"] == 0]
        wet = [row for row in rainfall if row["rain_group"] >= 2]
        dry_index, wet_index = [
            float(np.mean([row["calendar_index"] for row in group]))
            for group in [dry, wet]
        ]
        hourly = [
            row
            for row in tables["hourly"]
            if row["rider_type"] == rider and not row["is_weekend"]
        ]
        summary["riders"][rider] = {
            **description,
            "weekend_weekday_ratio": weekend["mean_daily_rides"]
            / weekday["mean_daily_rides"],
            "weekday_peak_hour_share": sum(
                row["share"] for row in hourly if row["hour"] in scope["peak_hours"]
            ),
            "dry_days": len(dry),
            "wet_days": len(wet),
            "dry_calendar_index": dry_index,
            "wet_calendar_index": wet_index,
            "wet_dry_index_ratio": wet_index / dry_index,
        }
    member = {
        row["trip_date"]: row["median_duration_min"]
        for row in days
        if row["rider_type"] == "member"
    }
    gaps = [
        row["median_duration_min"] - member[row["trip_date"]]
        for row in days
        if row["rider_type"] == "casual"
        and row["median_duration_min"] is not None
        and member.get(row["trip_date"]) is not None
    ]
    summary["paired_daily_duration"] = {
        "n_days": len(gaps),
        "median_gap_min": float(np.median(gaps)),
        "casual_longer_days": sum(value > 0 for value in gaps),
    }
    stations = tables["stations"]
    summary["stations"] = {
        "start_ids": len(stations),
        "multiple_names": sum(row["name_variants"] > 1 for row in stations),
        "mapped_ids": sum(
            row["latitude"] is not None and row["longitude"] is not None
            for row in stations
        ),
        "top10_start_share": sum(row["rides"] for row in stations[:10])
        / summary["rides"],
    }
    monthly = {
        (row["month"], row["rider_type"]): row["weekend_weekday_ratio"]
        for row in tables["monthly_weekend"]
    }
    summary["months_casual_weekend_ratio_above_member"] = sum(
        monthly[(month, "casual")] > monthly[(month, "member")]
        for month in range(1, 13)
    )
    return summary


def validate(tables, summary, scope):
    total = summary["rides"]
    if (
        total != sum(row["rides"] for row in tables["monthly"])
        or total != sum(row["rides"] for row in tables["hourly"])
        or total != sum(row["rides"] for row in tables["stations"])
    ):
        raise ValueError("月度、小时或站点汇总不守恒")
    if any(
        row["trip_date"].year != scope["exploration_year"] for row in tables["daily"]
    ):
        raise ValueError("日表越过探索年份范围")
    for rider in RIDERS:
        rows = [row for row in tables["duration_cdf"] if row["rider_type"] == rider]
        if not np.isclose(rows[-1]["cumulative_share"], 1):
            raise ValueError("时长累计分布未覆盖全部记录")
        observed = [
            row
            for row in tables["daily"]
            if row["rider_type"] == rider and row["source_observed"]
        ]
        if not np.isclose(np.mean([row["calendar_index"] for row in observed]), 100):
            raise ValueError("分层指数均值不为 100")


def main():
    from .plot_exploration import plot_all

    scope = json.loads(CONFIG.read_text())
    if (
        scope["exploration_year"] == scope["holdout_year"]
        or scope["holdout_behavior_and_weather_analysis"]
        or not 0 < scope["wet_threshold_mm"] < 5
    ):
        raise ValueError("探索/留出范围或降水分箱参数无效")
    prep_path = ROOT / "data/manifests/preparation.json"
    prep = json.loads(prep_path.read_text())
    if prep != json.loads((ROOT / "data/processed/preparation.json").read_text()):
        raise ValueError("数据整理版本与清单不一致")
    for record in prep["outputs"]:
        path = ROOT / "data/processed" / record["path"]
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"整理后数据与清单不符：{record['path']}")
    base = ROOT / "results"
    base.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix=".eda-", dir=base) as directory:
        stage = Path(directory)
        connection = duckdb.connect(
            config={
                "threads": 4,
                "memory_limit": "4GB",
                "temp_directory": str(stage / ".spill"),
            }
        )
        tables = compute_tables(connection, scope, prep)
        connection.close()
        shutil.rmtree(stage / ".spill", ignore_errors=True)
        summary = summarize(tables, scope)
        validate(tables, summary, scope)
        for name, rows in tables.items():
            export_table(stage / "tables" / f"{name}.csv", rows)
        write_json(stage / "summary.json", summary)
        font = plot_all(tables, summary, scope, stage / "figures")
        manifest = {
            "generated_at": utc_now(),
            "scope": scope,
            "preparation_manifest_sha256": sha256(prep_path),
            "config_sha256": sha256(CONFIG),
            "duckdb_version": duckdb.__version__,
            "numpy_version": np.__version__,
            "matplotlib_version": version("matplotlib"),
            "uv_lock_sha256": sha256(ROOT / "uv.lock"),
            "code_sha256": {
                name: sha256(Path(__file__).with_name(name))
                for name in ["explore_data.py", "plot_exploration.py"]
            },
            "font": font,
            "results": [
                {
                    "path": str(path.relative_to(stage)),
                    "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                }
                for path in sorted(stage.rglob("*"))
                if path.is_file()
            ],
        }
        write_json(stage / "run_manifest.json", manifest)
        destination = base / "eda"
        backup = base / (stage.name + "-previous")
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
    print(
        f"探索完成：{scope['exploration_year']} 年，{summary['rides']:,} 条行程；未分析 {scope['holdout_year']} 年关系。"
    )


if __name__ == "__main__":
    main()
