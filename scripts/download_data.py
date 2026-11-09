"""归档 Divvy 历史行程、NOAA 日天气及来源文档。"""

import argparse
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/manifests/downloads.json"
BUCKET = "https://divvy-tripdata.s3.amazonaws.com/"
NOAA = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/"
STATIONS = ("USW00094846", "USW00014819")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def download(source, previous=None, *, refresh=False, root=ROOT, attempts=3):
    """仅复用校验值匹配的文件；完整下载后才替换目标文件。"""
    path = root / source["path"]
    expected_size = source.get("expected_bytes")
    expected_etag = source.get("expected_etag")
    if (
        not refresh
        and previous
        and path.is_file()
        and previous["url"] == source["url"]
        and path.stat().st_size == previous["bytes"]
        and (expected_size is None or previous["bytes"] == expected_size)
        and (expected_etag is None or previous.get("etag") == expected_etag)
        and sha256(path) == previous["sha256"]
    ):
        return previous

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    for attempt in range(attempts):
        try:
            digest = hashlib.sha256()
            size = 0
            with urlopen(source["url"], timeout=60) as response:
                length = response.headers.get("Content-Length")
                etag = response.headers.get("ETag")
                with temporary.open("wb") as stream:
                    while chunk := response.read(1024 * 1024):
                        stream.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                if not size:
                    raise ValueError("响应为空")
                if length is not None and size != int(length):
                    raise ValueError(f"HTTP 长度不符：{size} != {length}")
                if expected_size is not None and size != expected_size:
                    raise ValueError(f"来源清单大小不符：{size} != {expected_size}")
                if expected_etag is not None and etag != expected_etag:
                    raise ValueError("来源 ETag 已变化，请重新获取清单")
                record = {
                    "path": source["path"],
                    "group": source["group"],
                    "url": source["url"],
                    "resolved_url": response.url,
                    "retrieved_at": utc_now(),
                    "last_modified": response.headers.get("Last-Modified"),
                    "etag": etag,
                    "bytes": size,
                    "sha256": digest.hexdigest(),
                }
            temporary.replace(path)
            return record
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt + 1 == attempts:
                raise
            time.sleep(2 ** (attempt + 1))


def discover_archives(save):
    """保留分页清单原文，获取所有 ZIP，避免手工枚举遗漏。"""
    namespace = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    query = {"list-type": "2"}
    archives = []
    page = 1
    while True:
        source = {
            "path": f"data/raw/sources/divvy_bucket_{page:03d}.xml",
            "group": "source_metadata",
            "url": BUCKET + "?" + urlencode(query),
        }
        record = save(source, refresh=True)
        tree = ET.parse(ROOT / record["path"]).getroot()
        for item in tree.findall("s:Contents", namespace):
            key = item.findtext("s:Key", namespaces=namespace)
            if key.lower().endswith(".zip"):
                if "/" in key or "\\" in key:
                    raise ValueError(f"需要检查非根目录的压缩包路径：{key}")
                archives.append(
                    {
                        "path": "data/raw/divvy/trips/" + key,
                        "group": "divvy_trips",
                        "url": BUCKET + quote(key),
                        "expected_bytes": int(
                            item.findtext("s:Size", namespaces=namespace)
                        ),
                        "expected_etag": item.findtext("s:ETag", namespaces=namespace),
                    }
                )
        if tree.findtext("s:IsTruncated", namespaces=namespace) != "true":
            break
        query["continuation-token"] = tree.findtext(
            "s:NextContinuationToken", namespaces=namespace
        )
        page += 1
    if not archives:
        raise ValueError("官方清单未列出任何 ZIP")
    return sorted(archives, key=lambda source: source["path"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4, help="并发下载数，默认 4")
    parser.add_argument(
        "--refresh", action="store_true", help="重新下载已缓存文件，获取更新快照"
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers 必须为正数")

    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    records = {record["path"]: record for record in previous.get("files", [])}
    manifest = {"started_at": utc_now(), "complete": False, "files": []}
    active = set()
    discovery_complete = False

    def persist():
        paths = active if discovery_complete else records
        manifest["files"] = [records[path] for path in sorted(paths) if path in records]
        write_json(MANIFEST, manifest)

    def save(source, refresh=None):
        active.add(source["path"])
        record = download(
            source,
            records.get(source["path"]),
            refresh=args.refresh if refresh is None else refresh,
        )
        records[source["path"]] = record
        persist()
        return record

    archives = discover_archives(save)
    manifest["expected_divvy_archives"] = len(archives)
    print(
        f"官方清单：{len(archives)} 个 ZIP，{sum(s['expected_bytes'] for s in archives) / 1024**3:.3f} GiB",
        flush=True,
    )

    sources = archives + [
        {
            "path": f"data/raw/noaa/{station}.csv.gz",
            "group": "noaa_weather",
            "url": NOAA + f"by_station/{station}.csv.gz",
        }
        for station in STATIONS
    ]
    documents = {
        "noaa_readme.txt": NOAA + "readme.txt",
        "noaa_readme_by_station.txt": NOAA + "readme-by_station.txt",
        "noaa_version.txt": NOAA + "ghcnd-version.txt",
        "noaa_stations.txt": NOAA + "ghcnd-stations.txt",
        "divvy_system_data.html": "https://divvybikes.com/system-data",
        "divvy_license.html": "https://divvybikes.com/data-license-agreement",
    }
    sources += [
        {"path": "data/raw/sources/" + name, "group": "source_metadata", "url": url}
        for name, url in documents.items()
    ]

    index = save(
        {
            "path": "data/raw/divvy/stations/gbfs.json",
            "group": "divvy_stations",
            "url": "https://gbfs.divvybikes.com/gbfs/gbfs.json",
        }
    )
    feeds = json.loads((ROOT / index["path"]).read_text())["data"]["en"]["feeds"]
    for feed in feeds:
        if feed["name"] in {
            "station_information",
            "station_status",
            "system_information",
        }:
            sources.append(
                {
                    "path": f"data/raw/divvy/stations/{feed['name']}.json",
                    "group": "divvy_stations",
                    "url": feed["url"],
                }
            )
    names = {
        Path(source["path"]).stem
        for source in sources
        if source["group"] == "divvy_stations"
    }
    if not {"station_information", "station_status", "system_information"} <= names:
        raise ValueError("GBFS 目录缺少所需站点接口")

    failures = []
    active.update(source["path"] for source in sources)
    discovery_complete = True
    persist()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {
            pool.submit(
                download, source, records.get(source["path"]), refresh=args.refresh
            ): source
            for source in sources
        }
        for index, future in enumerate(as_completed(pending), 1):
            source = pending[future]
            try:
                records[source["path"]] = future.result()
                print(f"[{index}/{len(sources)}] {source['path']}", flush=True)
            except Exception as error:
                records.pop(source["path"], None)
                failures.append({"path": source["path"], "error": str(error)})
                print(f"失败：{source['path']}：{error}", flush=True)
            persist()
    manifest.update(finished_at=utc_now(), complete=not failures, failures=failures)
    persist()
    if failures:
        raise SystemExit(f"{len(failures)} 个文件下载失败；重新运行可复用已校验文件")
    print(f"归档完成：{len(manifest['files'])} 个文件；请运行完整性检查。", flush=True)


if __name__ == "__main__":
    main()
