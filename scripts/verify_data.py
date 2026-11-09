"""离线检查原始文件、压缩包和天气覆盖，生成下载报告。"""

import csv
import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, timedelta
from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile

from .download_data import MANIFEST, ROOT, STATIONS, sha256, utc_now, write_json

ELEMENTS = ("PRCP", "SNWD", "SNOW", "AWND", "TMAX", "TMIN")
START_FIELDS = {
    "started_at",
    "starttime",
    "start_time",
    "01 - rental details local start time",
}


def archive_months(name):
    monthly = re.fullmatch(r"(\d{4})(\d{2})-divvy-tripdata\.zip", name)
    if monthly:
        return {f"{monthly[1]}-{monthly[2]}"}
    legacy = re.fullmatch(
        r"Divvy_(?:Stations_)?Trips_(\d{4})(?:[_-]Q([1-4])(?:Q([1-4]))?)?\.zip", name
    )
    if not legacy:
        raise ValueError(f"无法识别来源文件的时间范围：{name}")
    first = (int(legacy[2]) - 1) * 3 + 1 if legacy[2] else 1
    last = int(legacy[3] or legacy[2]) * 3 if legacy[2] else 12
    return {f"{legacy[1]}-{month:02d}" for month in range(first, last + 1)}


def start_index(columns):
    for index, column in enumerate(columns):
        if column.strip().lower() in START_FIELDS:
            return index
    return None


def trip_bounds(path):
    """扫描首尾来源包的出发日期，确定天气覆盖检查的边界。"""
    dates = set()
    invalid = 0
    with ZipFile(path) as archive:
        for member in archive.namelist():
            if not member.lower().endswith(".csv") or "__MACOSX" in member:
                continue
            with archive.open(member) as raw:
                rows = csv.reader(
                    TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")
                )
                index = start_index(next(rows, []))
                if index is None:
                    continue
                for row in rows:
                    try:
                        value = row[index].strip().split()[0].split("T")[0]
                        if "/" in value:
                            month, day, year = map(int, value.split("/"))
                            value = date(year, month, day).isoformat()
                        dates.add(date.fromisoformat(value).isoformat())
                    except (ValueError, IndexError):
                        invalid += 1
    if not dates:
        raise ValueError(f"未找到有效行程日期：{path.name}")
    return {
        "first_date": min(dates),
        "last_date": max(dates),
        "invalid_start_dates": invalid,
    }


def date_ranges(days):
    ranges = []
    for day in sorted(days):
        if ranges and day == date.fromisoformat(ranges[-1][1]) + timedelta(days=1):
            ranges[-1][1] = day.isoformat()
        else:
            ranges.append([day.isoformat(), day.isoformat()])
    return ranges


def weather_coverage(path, station, first_date, last_date):
    available = defaultdict(set)
    flagged = defaultdict(set)
    all_dates = set()
    elements = Counter()
    records = 0
    with gzip.open(path, "rt", encoding="ascii", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) != 8 or row[0] != station:
                raise ValueError(f"NOAA 格式或站点不符：{path.name}，行 {records + 1}")
            day = date.fromisoformat(row[1])
            value = int(row[3])
            records += 1
            elements[row[2]] += 1
            all_dates.add(day)
            if first_date <= day <= last_date and row[2] in ELEMENTS and value != -9999:
                available[row[2]].add(day)
                if row[5].strip():
                    flagged[row[2]].add(day)
    expected = {
        first_date + timedelta(days=i) for i in range((last_date - first_date).days + 1)
    }
    return {
        "records": records,
        "first_date": min(all_dates).isoformat(),
        "last_date": max(all_dates).isoformat(),
        "elements": sorted(elements),
        "trip_period_days": len(expected),
        "coverage": {
            element: {
                "available_days": len(available[element]),
                "quality_flagged_days": len(flagged[element]),
                "missing_days": len(expected - available[element]),
                "missing_ranges": date_ranges(expected - available[element]),
                "quality_flagged_ranges": date_ranges(flagged[element]),
            }
            for element in ELEMENTS
        },
    }


def inspect_zip(path):
    rows = []
    with ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"ZIP CRC 不符：{path.name}/{bad_member}")
        for member in archive.infolist():
            if member.is_dir():
                continue
            columns = []
            kind = "other"
            if (
                member.filename.lower().endswith(".csv")
                and "__MACOSX" not in member.filename
            ):
                with archive.open(member) as raw:
                    columns = next(
                        csv.reader(
                            TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")
                        ),
                        [],
                    )
                kind = (
                    "trips"
                    if start_index(columns) is not None
                    else "stations"
                    if "station" in member.filename.lower()
                    else "other"
                )
            rows.append(
                {
                    "archive": path.name,
                    "member": member.filename,
                    "kind": kind,
                    "uncompressed_bytes": member.file_size,
                    "crc32": f"{member.CRC:08x}",
                    "columns": json.dumps(columns, ensure_ascii=False),
                }
            )
    if not any(row["kind"] == "trips" for row in rows):
        raise ValueError(f"来源包中未识别到行程 CSV：{path.name}")
    return rows


def render_report(summary):
    coverage = summary["trip_coverage"]
    lines = [
        "# 数据下载与完整性检查",
        "",
        f"检查时间（UTC）：{summary['verified_at']}。本文件由 `scripts.verify_data` 生成。",
        "",
        f"- 官方历史归档：{summary['archive_count']} 个 ZIP，全部通过大小、SHA-256 与完整 ZIP CRC 检查。",
        f"- 全部归档文件：{summary['file_count']} 个，合计 {summary['total_bytes'] / 1024**3:.3f} GiB。",
        f"- 行程时间边界：{coverage['first_date']} 至 {coverage['last_date']}，依据首尾来源包中的出发日期。",
        f"- 行程日期跨度含 {coverage['month_count']} 个月；缺少对应来源包的月份：{', '.join(coverage['missing_months']) or '无'}。",
        f"- 历史站点 CSV：{summary['historical_station_tables']} 份，保存在原始 ZIP 内；当前 GBFS station_information 快照含 {summary['gbfs']['station_count']} 个站点。",
        "",
        "## 天气覆盖",
        "",
        "天气使用 NOAA GHCN-Daily 的完整站点档案；下表仅检查行程日期范围内的覆盖。可用天数包含带质量标记的值，后续清洗需单独判断；缺失包括未提供或值为 -9999。",
        "",
        "| 站点 | 档案日期范围 | 观测记录数 |",
        "| --- | --- | --- |",
    ]
    for station, weather in summary["noaa"].items():
        lines.append(
            f"| {station} | {weather['first_date']} 至 {weather['last_date']} | {weather['records']} |"
        )
    lines += [
        "",
        "| 站点 | 变量 | 可用天数 / 总天数 | 质量标记天数 | 缺失天数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for station, weather in summary["noaa"].items():
        for element, item in weather["coverage"].items():
            lines.append(
                f"| {station} | {element} | {item['available_days']} / {weather['trip_period_days']} | {item['quality_flagged_days']} | {item['missing_days']} |"
            )
    lines += [
        "",
        "## 核验范围与来源",
        "",
        "- 文件清单、下载时间、来源 URL 和校验值见 [downloads.json](../data/manifests/downloads.json)；包内文件及字段见 [zip_contents.csv](../data/manifests/zip_contents.csv)。",
        "- 天气缺失日期范围与检查详情见 [coverage.json](../data/manifests/coverage.json)。",
        "- 月份检查依据官方包名；仅扫描首尾包确定实际日期边界，尚未逐行核实所有年份、去重或清洗。",
        "- GBFS 是获取时的当前状态，不作为历史站点名录。",
        "",
        "来源：[Divvy](https://divvybikes.com/system-data)、[官方归档](https://divvy-tripdata.s3.amazonaws.com/)、[NOAA GHCN-Daily](https://www.ncei.noaa.gov/pub/data/ghcn/daily/)、[NOAA 格式说明](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme-by_station.txt)。原始数据和来源文档保存在服务器。",
        "",
    ]
    return "\n".join(lines)


def main():
    manifest = json.loads(MANIFEST.read_text())
    if not manifest.get("complete") or manifest.get("failures"):
        raise ValueError("下载清单未完成，请先处理失败文件")
    records = manifest["files"]
    if len({record["path"] for record in records}) != len(records):
        raise ValueError("下载清单包含重复路径")
    for record in records:
        path = ROOT / record["path"]
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"大小或 SHA-256 不符：{record['path']}")
    print(f"{len(records)} 个文件的大小与 SHA-256 均匹配。", flush=True)

    archives = [
        ROOT / record["path"] for record in records if record["group"] == "divvy_trips"
    ]
    namespace = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    listed = {}
    for record in records:
        if Path(record["path"]).name.startswith("divvy_bucket_"):
            for item in (
                ET.parse(ROOT / record["path"])
                .getroot()
                .findall("s:Contents", namespace)
            ):
                name = item.findtext("s:Key", namespaces=namespace)
                if name.lower().endswith(".zip"):
                    listed[name] = {
                        "bytes": int(item.findtext("s:Size", namespaces=namespace)),
                        "etag": item.findtext("s:ETag", namespaces=namespace),
                    }
    if (
        set(listed) != {path.name for path in archives}
        or len(archives) != manifest["expected_divvy_archives"]
    ):
        raise ValueError("本地归档与官方分页清单不一致")
    for record in records:
        if record["group"] == "divvy_trips":
            expected = listed[Path(record["path"]).name]
            if (
                record["bytes"] != expected["bytes"]
                or record["etag"] != expected["etag"]
            ):
                raise ValueError(f"大小或版本与官方清单不符：{record['path']}")

    members = []
    for index, path in enumerate(archives, 1):
        members.extend(inspect_zip(path))
        if index % 10 == 0 or index == len(archives):
            print(f"ZIP CRC 检查：{index}/{len(archives)}", flush=True)
    oldest = min(archives, key=lambda path: min(archive_months(path.name)))
    newest = max(archives, key=lambda path: max(archive_months(path.name)))
    boundary = {
        path.name: trip_bounds(path) for path in dict.fromkeys([oldest, newest])
    }
    first = date.fromisoformat(boundary[oldest.name]["first_date"])
    last = date.fromisoformat(boundary[newest.name]["last_date"])
    expected_months = {
        f"{year:04d}-{month:02d}"
        for year in range(first.year, last.year + 1)
        for month in range(1, 13)
        if first.strftime("%Y-%m")
        <= f"{year:04d}-{month:02d}"
        <= last.strftime("%Y-%m")
    }
    supplied_months = set().union(*(archive_months(path.name) for path in archives))
    weather = {
        station: weather_coverage(
            ROOT / f"data/raw/noaa/{station}.csv.gz", station, first, last
        )
        for station in STATIONS
    }

    gbfs = {}
    for record in records:
        if record["group"] == "divvy_stations":
            data = json.loads((ROOT / record["path"]).read_text())
            if not {"data", "last_updated"} <= data.keys():
                raise ValueError(f"GBFS JSON 格式不符：{record['path']}")
            gbfs[Path(record["path"]).stem] = {
                "last_updated": data["last_updated"],
                "retrieved_at": record["retrieved_at"],
            }
            if Path(record["path"]).stem == "station_information":
                gbfs["station_count"] = len(data["data"]["stations"])

    summary = {
        "verified_at": utc_now(),
        "download_complete": True,
        "integrity_checks": {
            "official_listing": "matched",
            "sha256": "passed",
            "zip_crc": "passed",
            "gzip_crc": "passed",
        },
        "download_manifest_sha256": sha256(MANIFEST),
        "file_count": len(records),
        "archive_count": len(archives),
        "total_bytes": sum(record["bytes"] for record in records),
        "uncompressed_zip_bytes": sum(row["uncompressed_bytes"] for row in members),
        "historical_station_tables": sum(row["kind"] == "stations" for row in members),
        "trip_coverage": {
            "first_date": first.isoformat(),
            "last_date": last.isoformat(),
            "month_count": len(expected_months),
            "missing_months": sorted(expected_months - supplied_months),
            "boundary_archives": boundary,
        },
        "noaa": weather,
        "gbfs": gbfs,
    }
    output = ROOT / "data/manifests"
    with (output / "zip_contents.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(members[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(members)
    write_json(output / "coverage.json", summary)
    report = ROOT / "docs/data_download.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(summary))
    print(f"完整性检查完成，报告：{report.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
