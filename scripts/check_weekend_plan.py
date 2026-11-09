"""仅用发现年的天气和日期检查分层覆盖，不计算行程效应。"""

import csv
import json
import math
from collections import defaultdict
from datetime import date, timedelta

from .download_data import ROOT, sha256, utc_now, write_json
from .explore_data import export_table

RAIN_STATES = ("zero_without_trace", "trace_or_below_threshold", "at_least_threshold")


def precipitation_state(value, trace, threshold):
    if value is None or not math.isfinite(value) or value < 0:
        return None
    if value == 0:
        return None if trace is None else RAIN_STATES[1 if trace else 0]
    return RAIN_STATES[1 if value < threshold else 2]


def load_design_days(path, year):
    days = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            day = date.fromisoformat(row["trip_date"])
            if day.year != year:
                raise ValueError("设计检查输入包含发现年以外的日期")
            if row["rider_type"] != "member":
                continue
            if day in days:
                raise ValueError("设计检查输入包含重复日期")
            temp = float(row["temp_mid_c"]) if row["temp_mid_c"] else None
            rain = float(row["prcp_mm"]) if row["prcp_mm"] else None
            trace = {"True": True, "False": False, "": None}[row["prcp_trace"]]
            days[day] = {
                "date": day,
                "temp_mid_c": temp,
                "prcp_mm": rain,
                "trace": trace,
            }
    return list(days.values())


def weather_support(days, width, minimum_weekend, minimum_weekday, threshold):
    cells = defaultdict(list)
    missing = 0
    for row in days:
        temp = row["temp_mid_c"]
        rain = precipitation_state(row["prcp_mm"], row["trace"], threshold)
        if temp is None or not math.isfinite(temp) or rain is None:
            missing += 1
            continue
        key = (row["date"].month, math.floor(temp / width) * width, rain)
        cells[key].append(row["date"])
    details = []
    supported = []
    for (month, left, rain), dates in sorted(cells.items()):
        weekends = sum(day.weekday() >= 5 for day in dates)
        weekdays = len(dates) - weekends
        keep = weekends >= minimum_weekend and weekdays >= minimum_weekday
        details.append(
            {
                "month": month,
                "temperature_left_c": left,
                "temperature_right_c": left + width,
                "precipitation_state": rain,
                "weekday_days": weekdays,
                "weekend_days": weekends,
                "supported": keep,
            }
        )
        if keep:
            supported.extend(dates)
    return {
        "temperature_width_c": width,
        "minimum_weekend_days": minimum_weekend,
        "minimum_weekday_days": minimum_weekday,
        "nonempty_cells": len(cells),
        "supported_cells": sum(row["supported"] for row in details),
        "supported_days": len(supported),
        "coverage_fraction": len(supported) / len(days),
        "supported_weekend_days": sum(day.weekday() >= 5 for day in supported),
        "supported_weekday_days": sum(day.weekday() < 5 for day in supported),
        "covered_months": len({day.month for day in supported}),
        "missing_weather_days": missing,
    }, details


def calendar_week_blocks(year):
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    weeks = defaultdict(list)
    for index in range((end - start).days):
        day = start + timedelta(days=index)
        weeks[day - timedelta(days=day.weekday())].append(day)
    return [
        {
            "monday_date": str(monday),
            "first_observed_date": str(days[0]),
            "last_observed_date": str(days[-1]),
            "month_stratum": days[0].month,
            "n_days": len(days),
            "weekend_days": sum(day.weekday() >= 5 for day in days),
        }
        for monday, days in sorted(weeks.items())
    ]


def main():
    config_path = ROOT / "configs/weekend_validation.json"
    config = json.loads(config_path.read_text())
    source = ROOT / "results/eda/tables/daily.csv"
    eda = json.loads((ROOT / "results/eda/run_manifest.json").read_text())
    expected = next(
        row["sha256"] for row in eda["results"] if row["path"] == "tables/daily.csv"
    )
    if (
        sha256(source) != expected
        or config["discovery_year"] != eda["scope"]["exploration_year"]
        or config["validation_year"] != eda["scope"]["holdout_year"]
    ):
        raise ValueError("发现年输入、留出范围或校验值不匹配")
    days = load_design_days(source, config["discovery_year"])
    year = config["discovery_year"]
    if len(days) != (date(year + 1, 1, 1) - date(year, 1, 1)).days:
        raise ValueError("发现年设计输入未覆盖全年日期")
    experiment = config["weather_experiment"]
    comparisons = []
    for width in (5, 10):
        for minimum in (2, 3):
            result, _ = weather_support(
                days, width, minimum, minimum, experiment["wet_threshold_mm"]
            )
            comparisons.append(result)
    selected, details = weather_support(
        days,
        experiment["temperature_bin_width_c"],
        experiment["minimum_weekend_days_per_stratum"],
        experiment["minimum_weekday_days_per_stratum"],
        experiment["wet_threshold_mm"],
    )
    blocks = calendar_week_blocks(config["validation_year"])
    output = ROOT / "results/study_plan"
    export_table(output / "design_comparison_2024.csv", comparisons)
    export_table(output / "selected_weather_cells_2024.csv", details)
    export_table(output / "validation_calendar_blocks.csv", blocks)
    manifest = {
        "checked_at": utc_now(),
        "discovery_year": config["discovery_year"],
        "validation_year": config["validation_year"],
        "config_sha256": sha256(config_path),
        "source_csv_sha256": sha256(source),
        "script_sha256": sha256(ROOT / "scripts/check_weekend_plan.py"),
        "selection_used_trip_effects": False,
        "validation_weather_read": False,
        "validation_relationships_analyzed": False,
        "selected_design_2024": selected,
        "validation_calendar_only": {
            "days": sum(row["n_days"] for row in blocks),
            "weekend_days": sum(row["weekend_days"] for row in blocks),
            "weekday_days": sum(row["n_days"] - row["weekend_days"] for row in blocks),
            "week_blocks": len(blocks),
        },
        "outputs": [
            {"path": path.name, "sha256": sha256(path)}
            for path in sorted(output.glob("*.csv"))
        ],
    }
    write_json(output / "feasibility.json", manifest)
    print(
        f"仅发现年设计检查：{selected['supported_cells']} 格，{selected['supported_days']}/{len(days)} 天；验证年仅生成日历，未读取其天气或行程关系。"
    )


if __name__ == "__main__":
    main()
