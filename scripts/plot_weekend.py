"""绘制发现、跨年验证和共同支持天气分层的两张核心图。"""

import csv
import json

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.ticker import ScalarFormatter

from .download_data import ROOT
from .plot_exploration import COLOR, LABEL, save, setup_font

RAIN_LABEL = {
    "zero_without_trace": "无雨",
    "trace_or_below_threshold": "微量/<1 mm",
    "at_least_threshold": "≥1 mm",
}
RAIN_COLOR = dict(zip(RAIN_LABEL, ["#538878", "#C79844", "#6579AC"]))


def plot_validation(tables, summary, output):
    output.mkdir(parents=True, exist_ok=True)
    font = setup_font()
    discovery = json.loads((ROOT / "results/eda/summary.json").read_text())
    with (ROOT / "results/eda/tables/monthly_weekend.csv").open(newline="") as stream:
        old_monthly = {
            (int(row["month"]), row["rider_type"]): float(row["weekend_weekday_ratio"])
            for row in csv.DictReader(stream)
        }
    year, discovery_year = summary["validation_year"], summary["discovery_year"]
    figure, axes = plt.subplots(
        1, 2, figsize=(12, 4.5), gridspec_kw={"width_ratios": [1, 1.5]}
    )
    for index, rider in enumerate(("member", "casual")):
        positions = np.arange(2) + (index - 0.5) * 0.32
        values = [
            discovery["riders"][rider]["weekend_weekday_ratio"],
            summary["primary"][f"{rider}_ratio"],
        ]
        axes[0].bar(
            positions,
            [value if value is not None else np.nan for value in values],
            width=0.3,
            color=COLOR[rider],
            label=LABEL[rider],
        )
        for position, value in zip(positions, values):
            if value is not None:
                axes[0].text(
                    position, value + 0.04, f"{value:.2f}", ha="center", fontsize=11
                )
    axes[0].axhline(1, color="#888888", linewidth=0.8, linestyle="--")
    axes[0].set(
        xticks=[0, 1],
        xticklabels=[f"{discovery_year} 发现年", f"{year} 验证年"],
        ylabel="周末日均量 / 平日日均量",
        title="两组周末节律：发现与跨年复现",
    )
    axes[0].margins(y=0.18)
    axes[0].legend(frameon=False, ncol=2, loc="upper center", fontsize=9)
    axes[1].plot(
        range(1, 13),
        [
            old_monthly[month, "casual"] / old_monthly[month, "member"]
            for month in range(1, 13)
        ],
        "o--",
        color="#999999",
        label=f"{discovery_year} 发现年",
        markersize=4,
    )
    axes[1].plot(
        range(1, 13),
        [
            row["ratio_ratio"] if row["ratio_ratio"] is not None else np.nan
            for row in tables["monthly_effects"]
        ],
        "o-",
        color="#6D5592",
        label=f"{year} 验证年",
        markersize=5,
    )
    axes[1].axhline(1, color="#888888", linewidth=0.8, linestyle="--")
    axes[1].set(
        xticks=range(1, 13),
        xlabel="月份",
        ylabel="Q = 非会员比值 / 会员比值",
        title="月度比值之比（描述性）",
    )
    axes[1].legend(frameon=False)
    axes[1].grid(axis="y", alpha=0.2)
    save(
        figure,
        output,
        "01_discovery_and_validation",
        "周六、日为周末，平日含节假日；两年均沿用既定筛选。Q>1 表示非会员相对更偏向周末；月度结果不另作显著性检验。",
    )

    weather = summary["weather_experiment"]
    selected = [row for row in tables["weather_cells"] if row["supported"]]
    figure, axes = plt.subplots(
        1,
        2,
        figsize=(13, max(5.5, 0.18 * len(selected) + 2)),
        gridspec_kw={"width_ratios": [1, 1.35]},
    )
    names = [
        f"全年（{sum(summary['primary'][key] for key in ['weekday_days', 'weekend_days'])} 天）",
        f"共同日期：未分层（{weather['supported_days']} 天）",
        f"共同日期：分层汇总（{weather['supported_days']} 天）",
    ]
    for position, row in enumerate(tables["effect_comparison"]):
        effect = row["ratio_ratio"]
        if effect is None:
            axes[0].text(
                0.65, position, "不可估", transform=axes[0].get_yaxis_transform()
            )
            continue
        if row["lower"] is not None:
            axes[0].hlines(
                position, row["lower"], row["upper"], color="#6D5592", linewidth=2
            )
            axes[0].plot(
                [row["lower"], row["upper"]],
                [position, position],
                "|",
                color="#6D5592",
                markersize=12,
            )
        axes[0].plot(effect, position, "o", color="#6D5592", markersize=8)
        axes[0].annotate(
            f"{effect:.3f}",
            (effect, position),
            xytext=(0, 12),
            textcoords="offset points",
            ha="center",
            fontsize=10,
        )
    axes[0].set(
        yticks=range(3),
        yticklabels=names,
        ylim=(2.55, -0.6),
        title=f"{year} 年：全年与共同支持日期",
        xlabel="Q（仅全年附周块稳定性区间）",
    )
    axes[0].axvline(1, color="#888888", linestyle="--", linewidth=0.8)
    axes[0].grid(axis="x", alpha=0.15)
    axes[0].text(
        0,
        -0.19,
        f"共同支持覆盖 {weather['coverage_fraction']:.1%} 日期、{weather['covered_months']} 个月；\n分层汇总为按日期数加权的几何平均。",
        transform=axes[0].transAxes,
        fontsize=9,
        color="#555555",
    )
    labels = []
    for position, cell in enumerate(selected):
        labels.append(
            f"{cell['month']:02d}月 [{cell['temperature_left_c']:g},{cell['temperature_right_c']:g})°C · n={cell['weekday_days'] + cell['weekend_days']}"
        )
        if cell["ratio_ratio"] is not None:
            axes[1].scatter(
                cell["ratio_ratio"],
                position,
                s=25 + cell["day_weight"] * 450,
                color=RAIN_COLOR[cell["precipitation_state"]],
            )
        else:
            axes[1].text(
                0.75,
                position,
                "不可估",
                transform=axes[1].get_yaxis_transform(),
                fontsize=8,
            )
    axes[1].set(
        yticks=range(len(selected)),
        yticklabels=labels,
        title="共同支持格：月份 × 10°C × 降水",
        xlabel="每格 Q（描述性，对数轴）；点大小表示日期权重",
        xscale="log",
    )
    axes[1].tick_params(axis="y", labelsize=8)
    axes[1].invert_yaxis()
    axes[1].axvline(1, color="#888888", linestyle="--", linewidth=0.8)
    axes[1].xaxis.set_major_formatter(ScalarFormatter())
    axes[1].xaxis.set_minor_formatter(ScalarFormatter())
    axes[1].legend(
        handles=[
            Line2D([], [], marker="o", linestyle="", color=RAIN_COLOR[key], label=label)
            for key, label in RAIN_LABEL.items()
        ],
        frameon=False,
        fontsize=8,
        loc="lower right",
    )
    if not selected:
        axes[1].text(0.5, 0.5, "无共同支持格", ha="center", transform=axes[1].transAxes)
    save(
        figure,
        output,
        "02_primary_and_weather_strata",
        "仅全年附 95% 近似周块稳定性区间；其余点不附区间。共同支持每格至少 2 个周末日与 2 个平日；三个汇总的日期范围或权重不同。",
    )
    return font
