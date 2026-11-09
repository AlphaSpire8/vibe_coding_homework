import csv
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check_weekend_plan import (
    calendar_week_blocks,
    load_design_days,
    precipitation_state,
    weather_support,
)


class WeekendPlanTests(unittest.TestCase):
    def test_precipitation_trace_threshold_and_missing(self):
        self.assertEqual(precipitation_state(0, False, 1), "zero_without_trace")
        self.assertEqual(precipitation_state(0, True, 1), "trace_or_below_threshold")
        self.assertEqual(precipitation_state(0.9, False, 1), "trace_or_below_threshold")
        self.assertEqual(precipitation_state(1, False, 1), "at_least_threshold")
        self.assertIsNone(precipitation_state(None, False, 1))
        self.assertIsNone(precipitation_state(0, None, 1))
        self.assertIsNone(precipitation_state(-1, False, 1))

    def test_weather_support_counts_dates_and_keeps_bin_boundaries(self):
        days = [
            {
                "date": date(2024, 1, day),
                "temp_mid_c": temp,
                "prcp_mm": 0,
                "trace": False,
            }
            for day, temp in [(6, 9), (7, 9), (8, 9), (9, 9), (10, 10), (11, -1)]
        ]
        summary, cells = weather_support(days, 10, 2, 2, 1)
        supported = [row for row in cells if row["supported"]]
        self.assertEqual(
            (summary["supported_cells"], summary["supported_days"]), (1, 4)
        )
        self.assertEqual(
            (summary["supported_weekend_days"], summary["supported_weekday_days"]),
            (2, 2),
        )
        self.assertEqual(supported[0]["temperature_left_c"], 0)
        self.assertEqual({row["temperature_left_c"] for row in cells}, {-10, 0, 10})

    def test_calendar_blocks_keep_partial_weeks_distinct(self):
        blocks = calendar_week_blocks(2025)
        self.assertEqual(len(blocks), 53)
        self.assertEqual(sum(row["n_days"] for row in blocks), 365)
        self.assertEqual(sum(row["weekend_days"] for row in blocks), 104)
        self.assertEqual(
            (blocks[0]["monday_date"], blocks[0]["month_stratum"], blocks[0]["n_days"]),
            ("2024-12-30", 1, 5),
        )
        self.assertEqual(
            (
                blocks[-1]["monday_date"],
                blocks[-1]["month_stratum"],
                blocks[-1]["n_days"],
            ),
            ("2025-12-29", 12, 3),
        )

    def test_loader_needs_no_outcomes_and_rejects_other_year(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "covariates.csv"
            columns = ["trip_date", "rider_type", "temp_mid_c", "prcp_mm", "prcp_trace"]
            with path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerow(["2024-01-01", "member", 10, 0, "False"])
                writer.writerow(["2024-01-01", "casual", 10, 0, "False"])
            self.assertEqual(len(load_design_days(path, 2024)), 1)
            with path.open("a", newline="") as stream:
                csv.writer(stream).writerow(["2025-01-01", "casual", 10, 0, "False"])
            with self.assertRaisesRegex(ValueError, "发现年以外"):
                load_design_days(path, 2024)


if __name__ == "__main__":
    unittest.main()
