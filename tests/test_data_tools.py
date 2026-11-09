import gzip
import json
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from zipfile import ZipFile

from scripts import download_data
from scripts.download_data import download, sha256, write_json
from scripts.verify_data import archive_months, inspect_zip, weather_coverage


@contextmanager
def local_source(body, declared_size=None):
    class Handler(BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header(
                "Content-Length",
                str(len(body) if declared_size is None else declared_size),
            )
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/data", Handler
        finally:
            server.shutdown()
            thread.join()


class DownloadTests(unittest.TestCase):
    def test_incomplete_download_preserves_existing_file(self):
        with TemporaryDirectory() as directory, local_source(b"short", 100) as (url, _):
            root = Path(directory)
            path = root / "data.bin"
            path.write_bytes(b"previous snapshot")
            source = {"path": path.name, "group": "fixture", "url": url}
            with self.assertRaises(ValueError):
                download(source, root=root, attempts=1)
            self.assertEqual(path.read_bytes(), b"previous snapshot")
            self.assertFalse((root / "data.bin.part").exists())

    def test_cache_checks_contents_even_when_size_matches(self):
        with (
            TemporaryDirectory() as directory,
            local_source(b"original") as (url, handler),
        ):
            root = Path(directory)
            source = {"path": "data.bin", "group": "fixture", "url": url}
            first = download(source, root=root)
            self.assertEqual(download(source, first, root=root), first)
            self.assertEqual(handler.requests, 1)
            (root / "data.bin").write_bytes(b"modified")
            second = download(source, first, root=root)
            self.assertEqual(handler.requests, 2)
            self.assertEqual(second["sha256"], first["sha256"])
            self.assertEqual((root / "data.bin").read_bytes(), b"original")

    def test_archive_size_must_match_source_inventory(self):
        with TemporaryDirectory() as directory, local_source(b"content") as (url, _):
            source = {
                "path": "data.bin",
                "group": "fixture",
                "url": url,
                "expected_bytes": 100,
            }
            with self.assertRaisesRegex(ValueError, "来源清单大小不符"):
                download(source, root=Path(directory), attempts=1)
            self.assertFalse((Path(directory) / "data.bin").exists())


class ManifestTests(unittest.TestCase):
    def test_discovery_failure_preserves_cached_records(self):
        for failure in ("listing", "gbfs", "interrupt"):
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "downloads.json"
                cached = []
                for name in (
                    "noaa/weather.csv.gz",
                    "divvy/stations/station_information.json",
                ):
                    path = root / "data/raw" / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"original snapshot")
                    cached.append(
                        {
                            "path": str(path.relative_to(root)),
                            "url": "https://example.test/" + name,
                            "bytes": path.stat().st_size,
                            "sha256": sha256(path),
                            "retrieved_at": "2026-01-01T00:00:00+00:00",
                        }
                    )
                write_json(manifest, {"complete": True, "files": cached})

                page = {
                    "path": "data/raw/sources/divvy_bucket_001.xml",
                    "group": "source_metadata",
                    "url": "https://example.test/list",
                }

                def discover(save):
                    save(page, refresh=True)
                    if failure == "interrupt":
                        raise KeyboardInterrupt()
                    if failure == "listing":
                        raise OSError("second listing page failed")
                    return [
                        {
                            "path": "data/raw/divvy/trips/202401-divvy-tripdata.zip",
                            "expected_bytes": 1,
                        }
                    ]

                def fetch(source, *args, **kwargs):
                    if source["path"].endswith("gbfs.json"):
                        raise OSError("GBFS index failed")
                    return {**source, "bytes": 1, "sha256": "page checksum"}

                with (
                    patch.object(download_data, "ROOT", root),
                    patch.object(download_data, "MANIFEST", manifest),
                    patch.object(
                        download_data, "discover_archives", side_effect=discover
                    ),
                    patch.object(download_data, "download", side_effect=fetch),
                    patch("sys.argv", ["download_data"]),
                    redirect_stdout(StringIO()),
                ):
                    with self.assertRaises(
                        KeyboardInterrupt if failure == "interrupt" else OSError
                    ):
                        download_data.main()

                saved = json.loads(manifest.read_text())
                records = {record["path"]: record for record in saved["files"]}
                self.assertFalse(saved["complete"])
                for record in cached:
                    self.assertEqual(records.get(record["path"]), record)
                    self.assertEqual(
                        (root / record["path"]).read_bytes(), b"original snapshot"
                    )


class VerificationTests(unittest.TestCase):
    def test_corrupted_zip_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "data.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("trips.csv", "started_at\n2024-01-01 12:00:00\n")
            content = bytearray(path.read_bytes())
            content[content.index(b"2024-01-01")] ^= 1
            path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "ZIP CRC"):
                inspect_zip(path)

    def test_legacy_and_monthly_periods(self):
        cases = {
            "Divvy_Stations_Trips_2013.zip": {
                f"2013-{month:02d}" for month in range(1, 13)
            },
            "Divvy_Stations_Trips_2014_Q1Q2.zip": {
                f"2014-{month:02d}" for month in range(1, 7)
            },
            "Divvy_Trips_2015-Q1Q2.zip": {f"2015-{month:02d}" for month in range(1, 7)},
            "Divvy_Trips_2020_Q1.zip": {"2020-01", "2020-02", "2020-03"},
            "202610-divvy-tripdata.zip": {"2026-10"},
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(archive_months(name), expected)

    def test_weather_distinguishes_zero_flagged_and_missing(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "weather.csv.gz"
            with gzip.open(path, "wt") as stream:
                stream.write("USW00094846,20240101,PRCP,0,,,W,2400\n")
                stream.write("USW00094846,20240102,PRCP,5,,S,W,2400\n")
                stream.write("USW00094846,20240103,PRCP,-9999,,,W,2400\n")
            result = weather_coverage(
                path, "USW00094846", date(2024, 1, 1), date(2024, 1, 3)
            )
            prcp = result["coverage"]["PRCP"]
            self.assertEqual(prcp["available_days"], 2)
            self.assertEqual(prcp["quality_flagged_days"], 1)
            self.assertEqual(prcp["missing_ranges"], [["2024-01-03", "2024-01-03"]])
            self.assertEqual(result["coverage"]["TMAX"]["missing_days"], 3)


if __name__ == "__main__":
    unittest.main()
