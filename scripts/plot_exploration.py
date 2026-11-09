"""将探索汇总绘制为可用于报告的中文 PNG/PDF。"""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter, ScalarFormatter

RIDERS = ("member", "casual")
LABEL = {
    "member": "会员",
    "casual": "非会员",
    "classic_bike": "普通自行车",
    "electric_bike": "电动自行车",
    "docked_bike": "docked_bike",
    "not_reported": "未提供",
}
COLOR = {"member": "#247C91", "casual": "#D68B27"}


def setup_font():
    for family in [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Microsoft YaHei",
        "SimHei",
        "Droid Sans Fallback",
    ]:
        try:
            font_manager.findfont(
                font_manager.FontProperties(family=family), fallback_to_default=False
            )
        except ValueError:
            continue
        plt.rcParams.update(
            {
                "font.family": family,
                "font.size": 10,
                "axes.unicode_minus": False,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "pdf.fonttype": 42,
                "savefig.dpi": 180,
            }
        )
        return family
    raise RuntimeError("需要安装中文字体，例如 Noto Sans CJK")


def save(figure, output, name, note):
    figure.text(0.01, 0.012, note, fontsize=8, color="#555555")
    figure.tight_layout(rect=(0, 0.055, 1, 0.98))
    figure.savefig(output / f"{name}.png", dpi=180, bbox_inches="tight")
    figure.savefig(
        output / f"{name}.pdf",
        bbox_inches="tight",
        metadata={"CreationDate": None, "ModDate": None},
    )
    plt.close(figure)


def plot_all(tables, summary, scope, output):
    output.mkdir(parents=True, exist_ok=True)
    font = setup_font()
    year = scope["exploration_year"]

    figure, axes = plt.subplots(
        1, 3, figsize=(12, 3.8), gridspec_kw={"width_ratios": [1.35, 1, 1]}
    )
    history = tables["coverage_history"]
    axes[0].plot(
        [r["year"] for r in history],
        [r["retention"] for r in history],
        "o-",
        color=COLOR["member"],
    )
    axes[0].axvspan(
        scope["holdout_year"] - 0.3,
        scope["holdout_year"] + 0.3,
        color="#DDDDDD",
        alpha=0.8,
    )
    axes[0].set(
        title="历史清洗保留率（仅质量背景）",
        xlabel="年份",
        ylabel="通过全部规则的比例",
        ylim=(0, 1.05),
    )
    axes[0].yaxis.set_major_formatter(PercentFormatter(1))
    axes[0].tick_params(axis="x", rotation=35)
    for ax, category, title in zip(
        axes[1:], ["vehicle", "rider"], [f"{year} 年：按车型", f"{year} 年：按用户标签"]
    ):
        rows = [
            r
            for r in tables["selection_quality"]
            if r["category"] == category and r["station_completeness"] is not None
        ]
        values = [r["station_completeness"] for r in rows]
        ax.barh(
            np.arange(len(rows)),
            values,
            color=[COLOR.get(r["label"], "#7A9199") for r in rows],
            height=0.55,
        )
        ax.set_yticks(
            np.arange(len(rows)), [LABEL.get(r["label"], r["label"]) for r in rows]
        )
        ax.set(title=title, xlim=(0, 1.16), xlabel="站点信息完整率")
        ax.xaxis.set_major_formatter(PercentFormatter(1))
        for y, value in enumerate(values):
            ax.text(value + 0.02, y, f"{value:.1%}", va="center", fontsize=9)
    save(
        figure,
        output,
        "01_selection_quality",
        f"右两图：满足其他规则时的站点完整率；{scope['holdout_year']} 年仅复用既有清洗计数，未分析其关系。",
    )

    figure, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    for rider in RIDERS:
        rows = [r for r in tables["monthly"] if r["rider_type"] == rider]
        axes[0].plot(
            [r["month"] for r in rows],
            [r["mean_daily_rides"] for r in rows],
            "-o",
            color=COLOR[rider],
            label=LABEL[rider],
            markersize=4,
        )
        for ax, weekend in zip(axes[1:], [False, True]):
            rows = [
                r
                for r in tables["hourly"]
                if r["rider_type"] == rider and r["is_weekend"] == weekend
            ]
            ax.plot(
                [r["hour"] for r in rows],
                [r["share"] for r in rows],
                color=COLOR[rider],
                label=LABEL[rider],
                linewidth=2,
            )
    axes[0].set(
        title=f"{year} 年月度日均行程",
        xlabel="月份",
        ylabel="日均行程数",
        xticks=[1, 3, 5, 7, 9, 11],
    )
    axes[0].legend(frameon=False)
    for ax, title in zip(axes[1:], ["平日小时分布", "周末小时分布"]):
        ax.set(
            title=title,
            xlabel="本地小时",
            ylabel="组内行程份额",
            xticks=[0, 6, 12, 18, 23],
        )
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(axis="y", alpha=0.2)
    save(
        figure,
        output,
        "02_time_patterns",
        "小时份额分别在本组、本日类型内归一化；平日指周一至周五，未单独剔除节假日。",
    )

    figure, ax = plt.subplots(figsize=(7, 4.3))
    for rider in RIDERS:
        rows = [r for r in tables["duration_cdf"] if r["rider_type"] == rider]
        median = summary["riders"][rider]["median_min"]
        ax.plot(
            [r["minute_upper"] for r in rows],
            [r["cumulative_share"] for r in rows],
            color=COLOR[rider],
            linewidth=2,
            label=f"{LABEL[rider]}：中位数 {median:.1f} 分钟",
        )
        ax.plot(median, 0.5, "o", color=COLOR[rider])
    ax.axhline(0.5, color="#999999", linestyle="--", linewidth=0.8)
    ax.set(
        xscale="log",
        xlim=(1, 1440),
        ylim=(0, 1.02),
        xlabel="行程时长（分钟，对数轴）",
        ylabel="累计行程比例",
        title=f"{year} 年：时长累计分布",
    )
    ax.set_xticks([1, 2, 5, 10, 20, 60, 240, 1440])
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(frameon=False, loc="lower right")
    ax.grid(alpha=0.2)
    save(
        figure,
        output,
        "03_duration_distribution",
        "以分钟上界绘制累计份额，覆盖完整的 1 分钟至 24 小时筛选范围；是行程分布，不是独立用户分布。",
    )

    figure, axes = plt.subplots(
        1, 2, figsize=(12, 5.5), gridspec_kw={"width_ratios": [1, 1.15]}
    )
    stations = tables["stations"]
    mapped = [
        r for r in stations if r["latitude"] is not None and r["longitude"] is not None
    ]
    if mapped:
        sizes = [r["rides"] / max(s["rides"] for s in mapped) * 600 for r in mapped]
        points = axes[0].scatter(
            [r["longitude"] for r in mapped],
            [r["latitude"] for r in mapped],
            s=sizes,
            c=[r["casual_share"] for r in mapped],
            vmin=0,
            vmax=1,
            cmap="viridis",
            alpha=0.75,
            edgecolors="white",
            linewidths=0.2,
        )
        figure.colorbar(
            points,
            ax=axes[0],
            shrink=0.75,
            label="非会员行程占比",
            format=PercentFormatter(1),
        )
    axes[0].set(title=f"{year} 年出发站点使用分布", xlabel="经度", ylabel="纬度")
    axes[0].set_aspect(1 / np.cos(np.deg2rad(41.9)))
    top = stations[:10][::-1]
    axes[1].barh(
        np.arange(len(top)), [r["rides"] for r in top], color="#9DBEC8", height=0.72
    )
    for y, row in enumerate(top):
        axes[1].text(200, y, row["display_name"], va="center", fontsize=8.5)
    axes[1].set(title="出发行程最多的 10 个站点 ID", xlabel="出发行程数", yticks=[])
    save(
        figure,
        output,
        "04_station_patterns",
        f"地图用当年行程坐标中位点，不回填当前 GBFS；气泡大小表示出发量；有效坐标 {len(mapped)}/{len(stations)} 个 ID。",
    )

    figure, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for rider in RIDERS:
        temperatures = [r for r in tables["temperature"] if r["rider_type"] == rider]
        axes[0].plot(
            [
                r["bin_left_c"] + scope["temperature_bin_width_c"] / 2
                for r in temperatures
            ],
            [r["mean_annual_index"] for r in temperatures],
            "-o",
            color=COLOR[rider],
            label=LABEL[rider],
            markersize=4,
        )
        for row in temperatures:
            if row["n_days"] < 10:
                axes[0].annotate(
                    f"n={row['n_days']}",
                    (
                        row["bin_left_c"] + scope["temperature_bin_width_c"] / 2,
                        row["mean_annual_index"],
                    ),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha="center",
                    fontsize=7,
                    color=COLOR[rider],
                )
        rain = {r["rain_group"]: r for r in tables["rain"] if r["rider_type"] == rider}
        axes[1].plot(
            range(5),
            [rain[i]["mean_calendar_index"] if i in rain else np.nan for i in range(5)],
            "-o",
            color=COLOR[rider],
            label=LABEL[rider],
        )
    axes[0].set(
        title="气温与行程量：原始季节关联",
        xlabel="高低温中点（°C，5°C 分箱）",
        ylabel="相对本组年均日行程指数（100 为均值）",
    )
    axes[0].legend(frameon=False)
    rain_n = {
        r["rain_group"]: r["n_days"]
        for r in tables["rain"]
        if r["rider_type"] == "member"
    }
    wet = scope["wet_threshold_mm"]
    names = ["无雨", f"微量/<{wet:g}", f"{wet:g}–<5", "5–<10", "≥10"]
    axes[1].set_xticks(
        range(5), [f"{name}\n(n={rain_n.get(i, 0)})" for i, name in enumerate(names)]
    )
    axes[1].set(
        title="降水与行程量：月及日类型分层",
        xlabel="日降水（mm）；n 为天数",
        ylabel="相对本月、本日类型均值指数（100 为均值）",
    )
    for ax in axes:
        ax.axhline(100, color="#888888", linestyle="--", linewidth=0.8)
        ax.grid(axis="y", alpha=0.15)
    save(
        figure,
        output,
        "05_weather_patterns",
        "无雨要求 PRCP=0 且无微量标记；气温中点不是实测日均温；分层仍未充分控制温度、假日及样本选择。",
    )

    figure, axes = plt.subplots(1, 2, figsize=(10, 3.4))
    features = ["temp_mid_c", "prcp_mm", "awnd_m_s", "snow_mm"]
    for ax, outcome, title in zip(
        axes, ["rides", "calendar_index"], ["原始日行程数", "按月、日类型归一化后"]
    ):
        values = np.array(
            [
                [
                    next(
                        r["spearman"]
                        for r in tables["correlations"]
                        if r["rider_type"] == rider
                        and r["feature"] == feature
                        and r["outcome"] == outcome
                    )
                    for feature in features
                ]
                for rider in RIDERS
            ],
            dtype=float,
        )
        heat = ax.imshow(values, vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")
        for i in range(2):
            for j in range(4):
                ax.text(
                    j,
                    i,
                    f"{values[i, j]:.2f}" if np.isfinite(values[i, j]) else "—",
                    ha="center",
                    va="center",
                    color="white" if abs(values[i, j]) > 0.55 else "#222222",
                )
        ax.set(
            title=title,
            xticks=range(4),
            xticklabels=["气温中点", "降水", "风速", "降雪"],
            yticks=range(2),
            yticklabels=[LABEL[r] for r in RIDERS],
        )
        figure.colorbar(heat, ax=ax, shrink=0.75, label="Spearman ρ")
    save(
        figure,
        output,
        "06_weather_correlations",
        "每组每天最多一个观测；相关系数不代表因果，降雪与低温等因素也互相相关。完整样本天数见统计表。",
    )
    return font
