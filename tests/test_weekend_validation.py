import math
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import duckdb

from scripts.validate_weekend import (
    bootstrap_ratio,
    load_days,
    primary_decision,
    ratio_summary,
    validate_days,
    weather_experiment,
)


def day(value, member, casual, **weather):
    return {
        "date": value,
        "member_rides": member,
        "casual_rides": casual,
        "temp_mid_c": 5,
        "prcp_mm": 0,
        "trace": False,
        **weather,
    }


def bootstrap_settings():
    return {
        "seed": 123,
        "replicates": 200,
        "minimum_valid_fraction": 0.99,
        "percentile_interval": [0.025, 0.975],
        "method": "test",
    }


def weather_settings():
    return {
        "temperature_bin_width_c": 10,
        "minimum_weekend_days_per_stratum": 2,
        "minimum_weekday_days_per_stratum": 2,
        "wet_threshold_mm": 1,
        "minimum_supported_strata_for_summary": 2,
    }


def weather_days():
    rows = [day(date(2025, 1, d), 10000, 500) for d in [4, 5, 6, 7]]
    rows += [
        day(date(2025, 2, d), 1, 4 if d in [1, 2] else 1) for d in [1, 2, 3, 4, 5, 6]
    ]
    rows += [day(date(2025, 3, 1), 1, 1, prcp_mm=None), day(date(2025, 3, 2), 1, 1)]
    return rows


class WeekendValidationTests(unittest.TestCase):
    def test_ratios_use_daily_means_and_zero_is_not_smoothed(self):
        rows = [day(date(2025, 1, d), 20, 10) for d in [6, 7, 8]] + [
            day(date(2025, 1, 11), 20, 40)
        ]
        result = ratio_summary(rows)
        self.assertEqual(
            (result["member_ratio"], result["casual_ratio"], result["ratio_ratio"]),
            (1, 4, 4),
        )
        rows[-1]["casual_rides"] = 0
        self.assertIsNone(ratio_summary(rows)["ratio_ratio"])

    def test_paired_bootstrap_preserves_proportional_groups_and_boundaries(self):
        rows = [
            day(date(2025, 1, 1) + timedelta(days=i), i + 1, 2 * (i + 1))
            for i in range(365)
        ]
        summary, blocks, draws = bootstrap_ratio(rows, bootstrap_settings())
        self.assertEqual(
            (summary["lower"], summary["upper"], summary["valid_fraction"]), (1, 1, 1)
        )
        self.assertTrue(all(math.isclose(row["ratio_ratio"], 1) for row in draws))
        self.assertEqual(
            (len(blocks), blocks[0]["n_days"], blocks[-1]["n_days"]), (53, 5, 3)
        )
        self.assertEqual(
            (blocks[0]["month_stratum"], blocks[-1]["month_stratum"]), (1, 12)
        )

    def test_bootstrap_keeps_each_months_block_count(self):
        rows = []
        for i in range(42):
            value = date(2025, 1, 6) + timedelta(days=i)
            january_block = (value - timedelta(days=value.weekday())).month == 1
            casual = (40 if value.weekday() >= 5 else 10) if january_block else 2
            rows.append(day(value, 100 if january_block else 10, casual))
        actual = ratio_summary(rows)["ratio_ratio"]
        summary, _, draws = bootstrap_ratio(rows, bootstrap_settings())
        self.assertTrue(all(math.isclose(row["ratio_ratio"], actual) for row in draws))
        self.assertAlmostEqual(summary["lower"], actual)
        self.assertAlmostEqual(summary["upper"], actual)

    def test_invalid_bootstrap_does_not_emit_interval_or_support(self):
        rows = [day(date(2025, 1, 6) + timedelta(days=i), 1, 0) for i in range(14)]
        summary, _, draws = bootstrap_ratio(rows, bootstrap_settings())
        self.assertEqual(summary["valid_fraction"], 0)
        self.assertIsNone(summary["lower"])
        self.assertTrue(all(row["ratio_ratio"] is None for row in draws))
        self.assertEqual(primary_decision(None, None), "not_estimable")
        self.assertEqual(primary_decision(1.1, 1), "direction_consistent_inconclusive")
        self.assertEqual(primary_decision(1.1, 1.01), "preliminary_support")
        self.assertEqual(primary_decision(1, 0.9), "not_supported")

    def test_weather_summary_weights_dates_not_trip_counts(self):
        rows = weather_days()
        summary, cells = weather_experiment(rows, weather_settings())
        self.assertEqual(
            (summary["supported_cells"], summary["supported_days"]), (2, 10)
        )
        self.assertAlmostEqual(summary["stratified_ratio_ratio"], 4**0.6)
        self.assertEqual(
            (summary["missing_weather_days"], summary["excluded_for_support_days"]),
            (1, 1),
        )
        self.assertEqual(len([cell for cell in cells if cell["supported"]]), 2)
        self.assertEqual(sum(row["weather_supported"] for row in rows), 10)
        self.assertEqual(
            rows[-2]["weather_exclusion_reason"], "weather_missing_or_unusable"
        )

    def test_zero_supported_cell_is_retained_and_invalidates_aggregate(self):
        rows = weather_days()
        for row in rows:
            if row["date"].month == 2 and row["date"].weekday() >= 5:
                row["casual_rides"] = 0
        summary, cells = weather_experiment(rows, weather_settings())
        self.assertEqual(
            (summary["supported_cells"], summary["supported_days"]), (2, 10)
        )
        self.assertEqual(summary["status"], "zero_supported_group_day_type_total")
        self.assertIsNone(summary["stratified_ratio_ratio"])
        self.assertEqual(len([cell for cell in cells if cell["supported"]]), 2)
        insufficient, _ = weather_experiment(
            weather_days(),
            {**weather_settings(), "minimum_supported_strata_for_summary": 5},
        )
        self.assertEqual(insufficient["status"], "insufficient_weather_support")
        self.assertIsNone(insufficient["stratified_ratio_ratio"])

    def test_data_loading_limits_year_and_distinguishes_zero_from_missing(self):
        with (
            TemporaryDirectory() as directory,
            duckdb.connect(config={"threads": 2}) as connection,
        ):
            root = Path(directory)
            for name in ["trips_all", "trips_clean"]:
                (root / "data/processed" / name).mkdir(parents=True)
            connection.execute("CREATE TABLE trips(trip_date DATE,rider_type VARCHAR)")
            connection.execute(
                "INSERT INTO trips VALUES ('2024-01-01','casual'),('2025-01-01','member'),('2026-01-01','casual')"
            )
            connection.execute(
                "COPY trips TO ? (FORMAT PARQUET)",
                [str(root / "data/processed/trips_clean/part.parquet")],
            )
            connection.execute("INSERT INTO trips VALUES ('2025-01-03','casual')")
            connection.execute(
                "COPY trips TO ? (FORMAT PARQUET)",
                [str(root / "data/processed/trips_all/part.parquet")],
            )
            connection.execute("""CREATE TABLE weather AS SELECT 'USW00094846' AS station_id,trip_date AS weather_date,
                0.0 AS prcp_mm,FALSE AS prcp_trace,10.0 AS tmax_c,0.0 AS tmin_c FROM trips GROUP BY trip_date""")
            connection.execute(
                "COPY weather TO ? (FORMAT PARQUET)",
                [str(root / "data/processed/weather_daily.parquet")],
            )
            connection.execute("DROP TABLE weather")
            with patch("scripts.validate_weekend.ROOT", root):
                rows = load_days(
                    connection, 2025, "data/processed/trips_clean/*.parquet"
                )
            self.assertEqual(len(rows), 365)
            self.assertEqual((rows[0]["member_rides"], rows[0]["casual_rides"]), (1, 0))
            self.assertIsNone(rows[1]["member_rides"])
            self.assertEqual((rows[2]["member_rides"], rows[2]["casual_rides"]), (0, 0))
            self.assertEqual(
                connection.execute(
                    "SELECT DISTINCT YEAR(weather_date) FROM weather"
                ).fetchall(),
                [(2025,)],
            )
            with self.assertRaisesRegex(ValueError, "无来源记录日"):
                validate_days(rows, 2025)


if __name__ == "__main__":
    unittest.main()
