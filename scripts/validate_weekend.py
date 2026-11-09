"""按 PR #5 冻结方案执行留出年验证，保留全部日期及分层审计。"""

import json
import math
import shutil
from collections import defaultdict
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import numpy as np

from .check_weekend_plan import precipitation_state, weather_support
from .download_data import ROOT, sha256, utc_now, write_json
from .explore_data import export_table, query_rows
from .prepare_data import literal

CONFIG = ROOT / "configs/weekend_validation.json"
RIDERS = ("member", "casual")
TOTAL_FIELDS = (
    "weekday_days",
    "weekend_days",
    "member_weekday_rides",
    "member_weekend_rides",
    "casual_weekday_rides",
    "casual_weekend_rides",
)


def load_days(connection, year, trip_dataset):
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    predicate = f"trip_date>=DATE {literal(start)} AND trip_date<DATE {literal(end)}"
    connection.execute(f"""CREATE TEMP TABLE clean_days AS
        SELECT trip_date,COUNT(*) AS clean_rows,
            COUNT(*) FILTER(WHERE rider_type='member') AS member_rides,
            COUNT(*) FILTER(WHERE rider_type='casual') AS casual_rides,
            COUNT(*) FILTER(WHERE rider_type NOT IN ('member','casual') OR rider_type IS NULL) AS unknown_rider_rows
        FROM read_parquet({literal(ROOT / trip_dataset)}) WHERE {predicate} GROUP BY trip_date""")
    connection.execute(f"""CREATE TEMP TABLE raw_days AS
        SELECT trip_date,COUNT(*) AS raw_rows
        FROM read_parquet({literal(ROOT / "data/processed/trips_all/*.parquet")})
        WHERE {predicate} GROUP BY trip_date""")
    connection.execute(f"""CREATE TEMP VIEW weather AS
        SELECT weather_date,prcp_mm,prcp_trace,tmax_c,tmin_c
        FROM read_parquet({literal(ROOT / "data/processed/weather_daily.parquet")})
        WHERE station_id='USW00094846' AND weather_date>=DATE {literal(start)} AND weather_date<DATE {literal(end)}""")
    if connection.execute(
        "SELECT COUNT(*) FROM (SELECT weather_date FROM weather GROUP BY weather_date HAVING COUNT(*)>1)"
    ).fetchone()[0]:
        raise ValueError("主站天气存在重复日期")
    return query_rows(
        connection,
        f"""WITH calendar AS (
        SELECT CAST(day AS DATE) AS trip_date
        FROM GENERATE_SERIES(DATE {literal(start)},DATE {literal(end)}-1,INTERVAL 1 DAY) t(day)
        ) SELECT c.trip_date AS date,MONTH(c.trip_date) AS month,ISODOW(c.trip_date)>=6 AS is_weekend,
            COALESCE(r.raw_rows,0) AS raw_rows,COALESCE(g.clean_rows,0) AS clean_rows,
            CASE WHEN r.raw_rows>0 THEN COALESCE(g.member_rides,0) END AS member_rides,
            CASE WHEN r.raw_rows>0 THEN COALESCE(g.casual_rides,0) END AS casual_rides,
            COALESCE(g.unknown_rider_rows,0) AS unknown_rider_rows,
            (w.tmax_c+w.tmin_c)/2 AS temp_mid_c,w.prcp_mm,w.prcp_trace AS trace
        FROM calendar c LEFT JOIN raw_days r USING(trip_date)
        LEFT JOIN clean_days g USING(trip_date) LEFT JOIN weather w ON c.trip_date=w.weather_date
        ORDER BY c.trip_date""",
    )


def validate_days(days, year):
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    expected = [start + timedelta(days=i) for i in range((end - start).days)]
    if [row["date"] for row in days] != expected:
        raise ValueError("验证日表未按唯一日期覆盖全年")
    missing = [str(row["date"]) for row in days if not row["raw_rows"]]
    if missing:
        raise ValueError(f"存在无来源记录日，暂停验证并核查：{missing}")
    for row in days:
        counts = [row[f"{rider}_rides"] for rider in RIDERS]
        if any(value is None or value < 0 for value in counts):
            raise ValueError("来源已观测日的组别计数无效")
        if (
            sum(counts) + row["unknown_rider_rows"] != row["clean_rows"]
            or row["clean_rows"] > row["raw_rows"]
        ):
            raise ValueError("日计数不守恒")


def ratio_summary(days):
    result = {
        f"{kind}_days": sum(
            row["date"].weekday() >= 5
            if kind == "weekend"
            else row["date"].weekday() < 5
            for row in days
        )
        for kind in ("weekday", "weekend")
    }
    for rider in RIDERS:
        for kind, weekend in [("weekday", False), ("weekend", True)]:
            total = sum(
                row[f"{rider}_rides"]
                for row in days
                if (row["date"].weekday() >= 5) == weekend
            )
            result[f"{rider}_{kind}_rides"] = total
            result[f"{rider}_{kind}_mean"] = (
                total / result[f"{kind}_days"] if result[f"{kind}_days"] else None
            )
        weekday, weekend = [
            result[f"{rider}_{kind}_mean"] for kind in ("weekday", "weekend")
        ]
        result[f"{rider}_ratio"] = (
            weekend / weekday if weekday and weekend is not None else None
        )
    estimable = all(result[key] > 0 for key in TOTAL_FIELDS)
    result["ratio_ratio"] = (
        result["casual_ratio"] / result["member_ratio"] if estimable else None
    )
    return result


def week_blocks(days):
    groups = defaultdict(list)
    for row in sorted(days, key=lambda row: row["date"]):
        groups[row["date"] - timedelta(days=row["date"].weekday())].append(row)
    return [
        {
            "monday_date": str(monday),
            "first_observed_date": str(group[0]["date"]),
            "last_observed_date": str(group[-1]["date"]),
            "month_stratum": group[0]["date"].month,
            "n_days": len(group),
            **{
                key: value
                for key, value in ratio_summary(group).items()
                if key in TOTAL_FIELDS
            },
        }
        for monday, group in sorted(groups.items())
    ]


def bootstrap_ratio(days, settings):
    blocks = week_blocks(days)
    groups = defaultdict(list)
    for block in blocks:
        groups[block["month_stratum"]].append([block[key] for key in TOTAL_FIELDS])
    rng = np.random.default_rng(settings["seed"])
    totals = np.zeros((settings["replicates"], len(TOTAL_FIELDS)), dtype=np.int64)
    for _, group in sorted(groups.items()):
        values = np.array(group, dtype=np.int64)
        indices = rng.integers(len(group), size=(settings["replicates"], len(group)))
        totals += values[indices].sum(axis=1)
    valid = np.all(totals > 0, axis=1)
    effects = np.full(settings["replicates"], np.nan)
    _, _, member_weekday, member_weekend, casual_weekday, casual_weekend = totals[
        valid
    ].T
    # 两组使用同一批日期，日类型天数在 Q 中抵消。
    effects[valid] = (casual_weekend / casual_weekday) / (
        member_weekend / member_weekday
    )
    valid &= np.isfinite(effects) & (effects > 0)
    valid_fraction = float(np.mean(valid))
    enough = valid.any() and valid_fraction >= settings["minimum_valid_fraction"]
    interval = (
        np.quantile(
            effects[valid], settings["percentile_interval"], method="linear"
        ).tolist()
        if enough
        else [None, None]
    )
    summary = {
        "method": settings["method"],
        "replicates": settings["replicates"],
        "seed": settings["seed"],
        "week_blocks": len(blocks),
        "valid_replicates": int(valid.sum()),
        "valid_fraction": valid_fraction,
        "lower": interval[0],
        "upper": interval[1],
        "interpretation": "approximate_date_block_stability",
    }
    draws = [
        {"replicate": index + 1, "ratio_ratio": float(value) if valid[index] else None}
        for index, value in enumerate(effects)
    ]
    return summary, blocks, draws


def primary_decision(effect, lower):
    if effect is None or lower is None:
        return "not_estimable"
    if effect <= 1:
        return "not_supported"
    return "preliminary_support" if lower > 1 else "direction_consistent_inconclusive"


def weather_experiment(days, settings):
    width = settings["temperature_bin_width_c"]
    support, cells = weather_support(
        days,
        width,
        settings["minimum_weekend_days_per_stratum"],
        settings["minimum_weekday_days_per_stratum"],
        settings["wet_threshold_mm"],
    )
    groups = defaultdict(list)
    for row in days:
        rain = precipitation_state(
            row["prcp_mm"], row["trace"], settings["wet_threshold_mm"]
        )
        temp = row["temp_mid_c"]
        if rain is None or temp is None or not math.isfinite(temp):
            row.update(
                weather_cell=None,
                weather_supported=False,
                weather_exclusion_reason="weather_missing_or_unusable",
            )
            continue
        key = (row["date"].month, math.floor(temp / width) * width, rain)
        groups[key].append(row)
    common = []
    for cell in cells:
        key = (cell["month"], cell["temperature_left_c"], cell["precipitation_state"])
        group = groups[key]
        cell_id = f"{key[0]:02d}|{key[1]:g}|{key[2]}"
        cell.update(cell_id=cell_id, **ratio_summary(group))
        cell["day_weight"] = (
            len(group) / support["supported_days"] if cell["supported"] else 0
        )
        for row in group:
            row.update(
                weather_cell=cell_id,
                weather_supported=cell["supported"],
                weather_exclusion_reason=""
                if cell["supported"]
                else "insufficient_day_type_support",
            )
        if cell["supported"]:
            common.extend(group)
    selected = [cell for cell in cells if cell["supported"]]
    status, effect = "estimable", None
    if len(selected) < settings["minimum_supported_strata_for_summary"]:
        status = "insufficient_weather_support"
    elif any(cell["ratio_ratio"] is None for cell in selected):
        status = "zero_supported_group_day_type_total"
    else:
        effect = math.exp(
            sum(cell["day_weight"] * math.log(cell["ratio_ratio"]) for cell in selected)
        )
    return {
        **support,
        "status": status,
        "stratified_ratio_ratio": effect,
        "common_support_unstratified": ratio_summary(common),
        "supported_cells_above_one": sum(
            cell["ratio_ratio"] is not None and cell["ratio_ratio"] > 1
            for cell in selected
        ),
        "zero_total_supported_cells": sum(
            cell["ratio_ratio"] is None for cell in selected
        ),
        "excluded_for_support_days": len(days)
        - support["missing_weather_days"]
        - len(common),
    }, cells


def check_inputs():
    design_path = ROOT / "results/study_plan/feasibility.json"
    design = json.loads(design_path.read_text())
    if sha256(CONFIG) != design["config_sha256"]:
        raise ValueError("验证配置不符合 PR #5 冻结校验值")
    config = json.loads(CONFIG.read_text())
    prep_path = ROOT / "data/manifests/preparation.json"
    prep = json.loads(prep_path.read_text())
    eda_path = ROOT / "results/eda/run_manifest.json"
    eda = json.loads(eda_path.read_text())
    if sha256(prep_path) != eda["preparation_manifest_sha256"] or prep != json.loads(
        (ROOT / "data/processed/preparation.json").read_text()
    ):
        raise ValueError("整理版本与发现年输入版本不一致")
    print("核对整理后文件大小与 SHA-256……", flush=True)
    for record in prep["outputs"]:
        path = ROOT / "data/processed" / record["path"]
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"整理数据与清单不符：{record['path']}")
    for record in eda["results"]:
        if record["path"] in ("summary.json", "tables/monthly_weekend.csv"):
            if sha256(ROOT / "results/eda" / record["path"]) != record["sha256"]:
                raise ValueError("发现年参考结果与校验值不符")
    return (
        config,
        prep,
        {
            "protocol_pr": "https://github.com/AlphaSpire8/vibe_coding_homework/pull/5",
            "config_sha256": sha256(CONFIG),
            "design_manifest_sha256": sha256(design_path),
            "preparation_manifest_sha256": sha256(prep_path),
            "eda_manifest_sha256": sha256(eda_path),
            "processed_files": prep["outputs"],
        },
    )


def main():
    from .plot_weekend import plot_validation

    config, prep, inputs = check_inputs()
    year = config["validation_year"]
    base = ROOT / "results"
    with TemporaryDirectory(prefix=".validation-", dir=base) as directory:
        stage = Path(directory)
        with duckdb.connect(config={"threads": 4, "memory_limit": "4GB"}) as connection:
            days = load_days(connection, year, config["trip_dataset"])
        validate_days(days, year)
        expected = next(
            row for row in prep["trips"]["yearly_rows"] if row["year"] == year
        )
        if (
            sum(row["clean_rows"] for row in days) != expected["rows_clean"]
            or sum(row["raw_rows"] for row in days) != expected["rows_all"]
        ):
            raise ValueError("全年原始/筛选计数与整理清单不守恒")
        primary = ratio_summary(days)
        uncertainty, blocks, draws = bootstrap_ratio(days, config["uncertainty"])
        primary["decision"] = primary_decision(
            primary["ratio_ratio"], uncertainty["lower"]
        )
        weather, cells = weather_experiment(days, config["weather_experiment"])
        monthly = [
            {
                "month": month,
                **ratio_summary([row for row in days if row["month"] == month]),
            }
            for month in range(1, 13)
        ]
        coverage = [
            {
                "month": month,
                "calendar_days": len(group),
                "supported_weekday_days": sum(
                    row["weather_supported"] and not row["is_weekend"] for row in group
                ),
                "supported_weekend_days": sum(
                    row["weather_supported"] and row["is_weekend"] for row in group
                ),
                "missing_weather_days": sum(
                    row["weather_exclusion_reason"] == "weather_missing_or_unusable"
                    for row in group
                ),
            }
            for month in range(1, 13)
            if (group := [row for row in days if row["month"] == month])
        ]
        common = weather["common_support_unstratified"]
        comparisons = [
            {
                "analysis": "full_year",
                "days": len(days),
                "ratio_ratio": primary["ratio_ratio"],
                "lower": uncertainty["lower"],
                "upper": uncertainty["upper"],
            },
            {
                "analysis": "common_support_unstratified",
                "days": weather["supported_days"],
                "ratio_ratio": common["ratio_ratio"],
                "lower": None,
                "upper": None,
            },
            {
                "analysis": "common_support_stratified",
                "days": weather["supported_days"],
                "ratio_ratio": weather["stratified_ratio_ratio"],
                "lower": None,
                "upper": None,
            },
        ]
        summary = {
            "validation_year": year,
            "discovery_year": config["discovery_year"],
            "execution_status": "completed",
            "source_unobserved_days": 0,
            "raw_rows": expected["rows_all"],
            "clean_rows": expected["rows_clean"],
            "unknown_rider_rows": sum(row["unknown_rider_rows"] for row in days),
            "primary": primary,
            "uncertainty": uncertainty,
            "weather_experiment": weather,
            "months_above_one": sum(
                row["ratio_ratio"] is not None and row["ratio_ratio"] > 1
                for row in monthly
            ),
        }
        tables = {
            "daily": days,
            "annual_rider_ratios": [
                {
                    "rider_type": rider,
                    "weekday_days": primary["weekday_days"],
                    "weekend_days": primary["weekend_days"],
                    **{
                        key.removeprefix(f"{rider}_"): value
                        for key, value in primary.items()
                        if key.startswith(f"{rider}_")
                    },
                }
                for rider in RIDERS
            ],
            "monthly_effects": monthly,
            "weather_cells": cells,
            "weather_coverage_by_month": coverage,
            "week_blocks": blocks,
            "bootstrap_draws": draws,
            "effect_comparison": comparisons,
        }
        for name, rows in tables.items():
            export_table(stage / "tables" / f"{name}.csv", rows)
        write_json(stage / "summary.json", summary)
        font = plot_validation(tables, summary, stage / "figures")
        manifest = {
            "generated_at": utc_now(),
            "execution_status": "completed",
            "frozen_protocol": config,
            "inputs": inputs,
            "duckdb_version": duckdb.__version__,
            "numpy_version": np.__version__,
            "matplotlib_version": version("matplotlib"),
            "uv_lock_sha256": sha256(ROOT / "uv.lock"),
            "code_sha256": {
                name: sha256(Path(__file__).with_name(name))
                for name in [
                    "validate_weekend.py",
                    "plot_weekend.py",
                    "check_weekend_plan.py",
                    "explore_data.py",
                    "plot_exploration.py",
                    "download_data.py",
                    "prepare_data.py",
                ]
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
        destination, backup = base / "validation", base / (stage.name + "-previous")
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
        f"验证完成：{year} 年，Q={primary['ratio_ratio']}；主判定 {primary['decision']}。"
    )


if __name__ == "__main__":
    main()
