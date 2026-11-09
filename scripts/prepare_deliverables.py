"""从已校验汇总生成两张汇报图，复制到独立的 TexPage 项目。"""

import csv
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np

from .download_data import ROOT, sha256, write_json
from .plot_exploration import COLOR, setup_font


def checked_inputs():
    inputs = []
    for group, names in {
        "eda": ["summary.json", "tables/monthly_weekend.csv"],
        "validation": ["summary.json"],
    }.items():
        base = ROOT / "results" / group
        manifest_path = base / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        inputs.append(
            {
                "path": str(manifest_path.relative_to(ROOT)),
                "sha256": sha256(manifest_path),
            }
        )
        for name in names:
            expected = next(
                row["sha256"] for row in manifest["results"] if row["path"] == name
            )
            if sha256(base / name) != expected:
                raise ValueError(f"汇报输入不符合运行校验值：{group}/{name}")
            inputs.append(
                {"path": str((base / name).relative_to(ROOT)), "sha256": expected}
            )
    old = json.loads((ROOT / "results/eda/summary.json").read_text())
    new = json.loads((ROOT / "results/validation/summary.json").read_text())
    with (ROOT / "results/eda/tables/monthly_weekend.csv").open(newline="") as stream:
        monthly = {
            (int(row["month"]), row["rider_type"]): float(row["weekend_weekday_ratio"])
            for row in csv.DictReader(stream)
        }
    return old, new, monthly, inputs


def save_pdf(figure, path):
    figure.tight_layout(pad=0.8)
    figure.savefig(
        path, bbox_inches="tight", metadata={"CreationDate": None, "ModDate": None}
    )
    plt.close(figure)


def render_figures(old, new, monthly, output):
    font = setup_font()
    plt.rcParams.update(
        {"font.size": 12, "axes.titlesize": 12, "axes.labelsize": 11, "pdf.fonttype": 3}
    )
    figure, axes = plt.subplots(
        1, 2, figsize=(8.4, 3.4), gridspec_kw={"width_ratios": [1, 1.4]}
    )
    ratios = [
        old["riders"][rider]["weekend_weekday_ratio"] for rider in ("member", "casual")
    ]
    axes[0].bar([0, 1], ratios, color=[COLOR["member"], COLOR["casual"]], width=0.58)
    axes[0].axhline(1, color="#999999", linewidth=0.8, linestyle="--")
    for index, value in enumerate(ratios):
        axes[0].text(index, value + 0.06, f"{value:.2f}", ha="center", fontsize=17)
    axes[0].set(
        xticks=[0, 1],
        xticklabels=["会员", "非会员"],
        ylabel="周末 / 平日日均行程比",
        ylim=(0, 2),
        title="2024 年：两组周末节律不同",
    )
    effects = [
        monthly[month, "casual"] / monthly[month, "member"] for month in range(1, 13)
    ]
    axes[1].plot(range(1, 13), effects, "o-", color="#6D5592", markersize=4)
    axes[1].axhline(1, color="#999999", linewidth=0.8, linestyle="--")
    axes[1].set(
        xticks=[1, 3, 5, 7, 9, 11],
        xlabel="月份",
        ylabel="非会员比值 / 会员比值 Q",
        ylim=(0.8, 2.8),
        title="2024 年：12 个月的 Q 均大于 1",
    )
    axes[1].grid(axis="y", alpha=0.15)
    save_pdf(figure, output / "discovery.pdf")

    primary, interval, weather = [
        new[key] for key in ("primary", "uncertainty", "weather_experiment")
    ]
    values = [
        primary["ratio_ratio"],
        weather["common_support_unstratified"]["ratio_ratio"],
        weather["stratified_ratio_ratio"],
    ]
    figure, ax = plt.subplots(figsize=(8.4, 3.2))
    ax.hlines(2, interval["lower"], interval["upper"], color="#6D5592", linewidth=2.5)
    ax.plot(
        [interval["lower"], interval["upper"]],
        [2, 2],
        "|",
        color="#6D5592",
        markersize=13,
    )
    for index, value in enumerate(values):
        ax.plot(
            value, 2 - index, "o", color="#6D5592", markersize=9 if index == 0 else 7
        )
        label = f"{value:.3f}" + (
            f"  [{interval['lower']:.3f}, {interval['upper']:.3f}]"
            if index == 0
            else ""
        )
        ax.annotate(
            label,
            (value, 2 - index),
            xytext=(0, 12),
            textcoords="offset points",
            ha="center",
            fontsize=12,
        )
    ax.axvline(1, color="#999999", linewidth=0.9, linestyle="--")
    ax.set(
        yticks=[2, 1, 0],
        yticklabels=[
            "全年未分层 · 365 天",
            f"共同日期未分层 · {weather['supported_days']} 天",
            f"共同日期分层汇总 · {weather['supported_days']} 天",
        ],
        xlim=(0.95, 2.35),
        ylim=(-0.5, 2.65),
        xticks=np.arange(1, 2.4, 0.2),
        xlabel="Q = 非会员的周末/平日比 ÷ 会员的周末/平日比",
    )
    ax.grid(axis="x", alpha=0.15)
    save_pdf(figure, output / "validation.pdf")
    return font


def main():
    old, new, monthly, inputs = checked_inputs()
    base = ROOT / "deliverables"
    base.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix=".figures-", dir=base) as directory:
        stage = Path(directory)
        font = render_figures(old, new, monthly, stage)
        outputs = []
        for project in ("slides", "report"):
            destination = base / project
            destination.mkdir(exist_ok=True)
            for name in ("discovery.pdf", "validation.pdf"):
                path = destination / name
                shutil.copyfile(stage / name, path)
                outputs.append(
                    {"path": str(path.relative_to(base)), "sha256": sha256(path)}
                )
    write_json(
        base / "asset_manifest.json",
        {
            "purpose": "presentation_only_no_new_statistical_analysis",
            "inputs": inputs,
            "code_sha256": sha256(Path(__file__)),
            "uv_lock_sha256": sha256(ROOT / "uv.lock"),
            "font": font,
            "outputs": outputs,
        },
    )
    print("已生成两张核心图，并放入独立的汇报与报告目录；未改写 tex 文件或统计结果。")


if __name__ == "__main__":
    main()
