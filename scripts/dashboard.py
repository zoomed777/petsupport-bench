"""评测仪表盘与进化曲线 (Evaluation Dashboard)

读取 results/full_results.csv，生成：
    1. 四配置消融对比图（柱状图：平均分 / 安全闸门触发率）
    2. 配置进化曲线图（折线图：从裸 Hy3 到完整护栏的分数提升）
    3. 失败模式饼图（按类别统计失败 case 占比）
    4. 维度雷达图（可选，完整配置下 7 维平均分）

输出到 results/dashboard/ 目录下的 PNG 文件。

不依赖 Streamlit，可直接命令行运行。
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Windows 中文字体设置
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

RESULTS_PATH = Path("results/full_results.csv")
OUTPUT_DIR = Path("results/dashboard")

# 配置进化顺序：从裸模型到完整系统
CONFIG_ORDER = ["A0", "A1", "A2", "A3"]
CONFIG_LABELS = {
    "A0": "裸 Hy3",
    "A1": "+RAG",
    "A2": "+RAG+追问",
    "A3": "完整管线",
}


def load_results(path: Path = RESULTS_PATH) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["total"] = float(row.get("total", 0) or 0)
            row["gate_triggered"] = row.get("gate_triggered", "").lower() == "true"
            rows.append(row)
    return rows


def aggregate_by_config(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("config", "unknown")].append(row)

    stats: dict[str, dict[str, Any]] = {}
    for cfg, cases in grouped.items():
        totals = [c["total"] for c in cases]
        gates = sum(1 for c in cases if c["gate_triggered"])
        stats[cfg] = {
            "count": len(cases),
            "avg_total": sum(totals) / len(totals),
            "min_total": min(totals),
            "max_total": max(totals),
            "gate_rate": gates / len(cases),
            "gate_count": gates,
        }
    return stats


def plot_evolution(stats: dict[str, dict[str, Any]], output_dir: Path) -> Path:
    """配置进化曲线：平均分 + 闸门触发率双轴。"""
    configs = [c for c in CONFIG_ORDER if c in stats]
    labels = [CONFIG_LABELS.get(c, c) for c in configs]
    avg_scores = [stats[c]["avg_total"] for c in configs]
    gate_rates = [stats[c]["gate_rate"] * 100 for c in configs]

    fig, ax1 = plt.subplots(figsize=(10, 5))
    color = "#2563eb"
    ax1.set_xlabel("配置")
    ax1.set_ylabel("平均分", color=color)
    ax1.plot(labels, avg_scores, color=color, marker="o", linewidth=2.5, markersize=8, label="平均分")
    ax1.tick_params(axis="y", labelcolor=color)
    ax1.set_ylim(0, 105)
    ax1.grid(axis="y", linestyle="--", alpha=0.4)

    ax2 = ax1.twinx()
    color2 = "#dc2626"
    ax2.set_ylabel("安全闸门触发率 (%)", color=color2)
    ax2.plot(labels, gate_rates, color=color2, marker="s", linewidth=2, markersize=7, label="闸门触发率")
    ax2.tick_params(axis="y", labelcolor=color2)
    ax2.set_ylim(0, 105)

    fig.suptitle("PetSupport-Bench：从裸模型到完整管线的进化曲线", fontsize=13, fontweight="bold")
    fig.tight_layout()
    out_path = output_dir / "evolution_curve.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_config_comparison(stats: dict[str, dict[str, Any]], output_dir: Path) -> Path:
    """配置消融对比柱状图。"""
    configs = [c for c in CONFIG_ORDER if c in stats]
    labels = [CONFIG_LABELS.get(c, c) for c in configs]
    avg_scores = [stats[c]["avg_total"] for c in configs]
    gate_rates = [stats[c]["gate_rate"] * 100 for c in configs]

    x = range(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 5))
    bars1 = ax.bar([i - width / 2 for i in x], avg_scores, width, label="平均分", color="#3b82f6")
    bars2 = ax.bar([i + width / 2 for i in x], gate_rates, width, label="闸门触发率 (%)", color="#ef4444")

    ax.set_ylabel("分数 / 百分比")
    ax.set_title("不同配置的平均分与安全闸门触发率对比", fontsize=13, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.set_ylim(0, 105)
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    # 标注数值
    for bar in bars1:
        height = bar.get_height()
        ax.annotate(f"{height:.1f}", xy=(bar.get_x() + bar.get_width() / 2, height), xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=9)
    for bar in bars2:
        height = bar.get_height()
        ax.annotate(f"{height:.1f}%", xy=(bar.get_x() + bar.get_width() / 2, height), xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=9)

    fig.tight_layout()
    out_path = output_dir / "config_comparison.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_failure_pie(rows: list[dict[str, Any]], output_dir: Path) -> Path:
    """失败模式饼图：按 category 统计总分低于 60 或触发闸门的 case。"""
    failed = [r for r in rows if r["total"] < 60 or r["gate_triggered"]]
    counter: dict[str, int] = defaultdict(int)
    for r in failed:
        counter[r.get("category", "unknown")] += 1

    if not counter:
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.text(0.5, 0.5, "暂无失败样本", ha="center", va="center", fontsize=14)
        ax.axis("off")
        out_path = output_dir / "failure_pie.png"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        return out_path

    labels = list(counter.keys())
    sizes = list(counter.values())

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90, textprops={"fontsize": 10})
    ax.set_title("失败样本类别分布", fontsize=13, fontweight="bold")
    fig.tight_layout()
    out_path = output_dir / "failure_pie.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_dimension_radar(rows: list[dict[str, Any]], output_dir: Path) -> Path:
    """完整配置下 7 维雷达图。"""
    full_rows = [r for r in rows if r.get("config") == "A3_full_pipeline"]
    if not full_rows:
        return output_dir / "dimension_radar.png"

    dims = ["D1_red_flag", "D2_triage", "D3_info_completeness", "D4_evidence", "D5_safety_boundary", "D6_actionability", "D7_format"]
    dim_labels = ["红旗识别", "分流", "信息", "证据", "安全", "可执行", "可理解"]
    averages = []
    for dim in dims:
        vals = [float(r.get(dim, 0) or 0) for r in full_rows if float(r.get(dim, -1) or -1) >= 0]
        averages.append(sum(vals) / len(vals) if vals else 0)

    angles = [n / float(len(dim_labels)) * 2 * 3.14159 for n in range(len(dim_labels))]
    angles += angles[:1]
    averages += averages[:1]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
    ax.plot(angles, averages, "o-", linewidth=2, color="#2563eb")
    ax.fill(angles, averages, alpha=0.25, color="#2563eb")
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dim_labels)
    ax.set_ylim(0, 4)
    ax.set_title("完整配置：7 维评分雷达图", fontsize=13, fontweight="bold", pad=20)
    fig.tight_layout()
    out_path = output_dir / "dimension_radar.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def generate_summary(stats: dict[str, dict[str, Any]], output_dir: Path) -> Path:
    """生成 dashboard 摘要 JSON。"""
    summary = {
        "config_count": len(stats),
        "configs": {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in stats.items()},
        "improvement": None,
    }
    if "A0" in stats and "A3" in stats:
        summary["improvement"] = round(stats["A3"]["avg_total"] - stats["A0"]["avg_total"], 2)

    out_path = output_dir / "dashboard_summary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return out_path


def main() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_results()
    stats = aggregate_by_config(rows)

    paths = {
        "evolution_curve": str(plot_evolution(stats, OUTPUT_DIR)),
        "config_comparison": str(plot_config_comparison(stats, OUTPUT_DIR)),
        "failure_pie": str(plot_failure_pie(rows, OUTPUT_DIR)),
        "dimension_radar": str(plot_dimension_radar(rows, OUTPUT_DIR)),
        "summary": str(generate_summary(stats, OUTPUT_DIR)),
    }
    return {"generated": paths, "stats": stats}


if __name__ == "__main__":
    info = main()
    print(json.dumps({"generated": info["generated"]}, ensure_ascii=False, indent=2))
