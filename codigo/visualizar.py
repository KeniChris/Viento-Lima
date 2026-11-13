"""Genera figuras de arquitectura y diagnóstico con los modelos seleccionados."""

import argparse
import json
from pathlib import Path
import textwrap

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .modelos_finales import load_bundle
from .pronosticar import TARGET, TARGET_TIME
from .viento import STATION


HORIZONS = (1, 3, 6)
BLUE = "#245371"
ORANGE = "#A4482E"
GRAY = "#506070"
LABELS = {
    "PM2_5": "PM2.5 actual",
    "pm_media_6h": "Media de PM2.5 en 6 h",
    "pm_media_24h": "Media de PM2.5 en 24 h",
    "pm_std_6h": "Desv. de PM2.5 en 6 h",
    "pm_desviacion_24h": "Desv. de PM2.5 en 24 h",
    "pm_cambio_1h": "Cambio de PM2.5 en 1 h",
    "pm_cambio_3h": "Cambio de PM2.5 en 3 h",
    "pm_mediana_3h": "Mediana de PM2.5 en 3 h",
    "pm_hora_objetivo_media_3dias": "Media de PM2.5 a la hora objetivo en 3 días",
    "vecinos_alza_3h": "Mayor alza vecina con rezago de 3 h",
    "hora_seno": "Seno de la hora objetivo",
    "viento_este_ms": "Viento hacia el este",
    "viento_norte_ms": "Viento hacia el norte",
    "ifs10_velocidad_ms": "Rapidez del viento",
    "temperature_2m": "Temperatura a 2 m",
    "relative_humidity_2m": "Humedad a 2 m",
    "pressure_msl": "Presión al nivel del mar",
    "precipitation": "Precipitación",
}


def feature_label(name):
    name = name.split("__", 1)[-1]
    if name.startswith("missingindicator_"):
        return "Indicador de ausencia de " + feature_label(name.removeprefix("missingindicator_"))
    if name.startswith("pm_lag_"):
        return "PM2.5 de hace " + name.removeprefix("pm_lag_").replace("h", " h")
    return LABELS.get(name, name.replace("_", " ").capitalize())


def save(fig, output, name):
    fig.savefig(output / f"{name}.png", dpi=135, bbox_inches="tight", pad_inches=.17, facecolor="white")
    plt.close(fig)


def tree_figure(pipeline, output):
    transform, model = pipeline.steps[0][1], pipeline.steps[1][1]
    names = transform.get_feature_names_out()
    tree = json.loads(model.get_booster().get_dump(dump_format="json")[0])
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.set(xlim=(-.55, 7.55), ylim=(-.42, 3.5))
    ax.set_axis_off()

    def draw(node, level=0, left=0, right=8, parent=None, decision=""):
        x, y = (left + right - 1) / 2, 3 - level
        if parent is not None:
            ax.annotate("", xy=(x, y + .15), xytext=(parent[0], parent[1] - .16),
                        arrowprops={"arrowstyle": "->", "color": GRAY, "lw": 1})
            ax.text((x + parent[0]) / 2, (y + parent[1]) / 2, decision,
                    fontsize=8, ha="center", va="center", backgroundcolor="white")
        if "leaf" in node:
            value = f"{node['leaf']:+.2f}".replace(".", ",")
            label = f"{value} µg/m³"
        else:
            name = feature_label(names[int(node["split"][1:])])
            condition = f"< {node['split_condition']:.2f}".replace(".", ",")
            label = "\n".join(textwrap.wrap(name, 26)) + f"\n{condition}"
            children = {n["nodeid"]: n for n in node["children"]}
            middle = (left + right) / 2
            draw(children[node["yes"]], level + 1, left, middle, (x, y), "Sí")
            draw(children[node["no"]], level + 1, middle, right, (x, y), "No")
        ax.text(x, y, label, ha="center", va="center", fontsize=8.5,
                bbox={"boxstyle": "round,pad=.37", "facecolor": "#E8F0F5" if "leaf" not in node else "#F3F6F7",
                      "edgecolor": BLUE, "linewidth": .8})

    draw(tree)
    save(fig, output, "arbol_xgboost")
    return {"variable_raiz": feature_label(names[int(tree["split"][1:])]),
            "umbral_raiz": tree["split_condition"]}


def lstm_figure(output):
    fig, ax = plt.subplots(figsize=(9.5, 3.7))
    ax.set(xlim=(0, 10), ylim=(0, 4.3))
    ax.set_axis_off()
    nodes = {
        "history": (1.45, 3.12, "24 pasos horarios\nPM2.5 e indicador de presencia"),
        "state": (4.15, 3.12, "LSTM\n16 unidades"),
        "inputs": (1.6, 1.48, "Rezagos, clima y calendario\nEntradas tabulares"),
        "dense": (6.5, 2.25, "Capa densa\n24 unidades"),
        "output": (8.9, 2.25, "Cambio de PM2.5\na 3 h o 6 h"),
    }
    for origin, target, x_begin, x_end in (
        ("history", "state", 1.45, .65), ("state", "dense", .60, .72),
        ("inputs", "dense", 1.42, .74), ("dense", "output", .67, 1.06),
    ):
        a, b = nodes[origin], nodes[target]
        ax.annotate("", xy=(b[0] - x_end, b[1]), xytext=(a[0] + x_begin, a[1]),
                    arrowprops={"arrowstyle": "->", "color": BLUE, "lw": 1.6})
    for x, y, label in nodes.values():
        ax.text(x, y, label, ha="center", va="center", fontsize=11,
                bbox={"boxstyle": "round,pad=.5", "facecolor": "#E8F0F5", "edgecolor": BLUE})
    ax.text(1.45, 3.87, "t−23 h, t−22 h, …, t−1 h, t", ha="center", va="center", color=GRAY, fontsize=10)
    save(fig, output, "arquitectura_lstm")


def episode_figure(rows, output):
    # El caso ilustrativo se fija por el máximo observado, sin elegirlo por el error.
    event = rows.loc[rows.horizonte_h.eq(1)].sort_values([TARGET, TARGET_TIME, STATION], ascending=[False, True, True]).iloc[0]
    time = event[TARGET_TIME]
    start, end = time - pd.Timedelta(hours=24), time + pd.Timedelta(hours=24)
    view = rows.loc[rows[STATION].eq(event[STATION]) & rows[TARGET_TIME].between(start, end)]
    timeline = pd.date_range(start, end, freq="h")
    fig, axes = plt.subplots(3, 1, figsize=(8.4, 5.9), sharex=True, sharey=True, layout="constrained")
    peak = []
    for horizon, ax in zip(HORIZONS, axes, strict=True):
        data = view.loc[view.horizonte_h.eq(horizon)].set_index(TARGET_TIME).reindex(timeline)
        ax.plot(timeline, data[TARGET], color=BLUE, lw=1.7, marker=".", label="Medido")
        ax.plot(timeline, data.pm25_estimado, color=ORANGE, lw=1.5, ls="--", label="Pronosticado")
        ax.axvline(time, color=GRAY, lw=.9, ls=":")
        ax.text(.97, .87, f"{horizon} h", transform=ax.transAxes, ha="right", color=GRAY)
        if pd.notna(data.loc[time, "pm25_estimado"]):
            peak.append({"horizonte_h": horizon, "estimado": float(data.loc[time, "pm25_estimado"])})
    axes[0].legend(loc="upper left", ncol=2, frameon=False)
    fig.supylabel("PM2.5 (µg/m³)")
    axes[-1].set_xlabel("Hora objetivo de Lima")
    save(fig, output, "episodio_observado")
    return {"estacion": event[STATION], "hora_objetivo": time.isoformat(), "pm25_observado": float(event[TARGET]),
            "pronosticos": peak}


def diagnostics(rows, output):
    fig, axes = plt.subplots(2, 3, figsize=(10.5, 6.2), sharex="row", sharey="row", layout="constrained")
    limit = max(rows[TARGET].max(), rows.pm25_estimado.max()) * 1.04
    residual_limit = (rows.pm25_estimado - rows[TARGET]).abs().max() * 1.04
    for i, horizon in enumerate(HORIZONS):
        data = rows.loc[rows.horizonte_h.eq(horizon)]
        actual, estimate = data[TARGET], data.pm25_estimado
        residual = estimate - actual
        top, bottom = axes[:, i]
        top.scatter(actual, estimate, color=BLUE, s=4, alpha=.12, linewidths=0)
        top.plot([0, limit], [0, limit], color=GRAY, ls="--", lw=1)
        top.set(xlim=(0, limit), ylim=(0, limit), title=f"{horizon} h", xlabel="Medido (µg/m³)")
        bottom.scatter(estimate, residual, color=BLUE, s=4, alpha=.12, linewidths=0)
        bottom.scatter(estimate[data.alta], residual[data.alta], color=ORANGE, s=8, alpha=.45, linewidths=0)
        bottom.axhline(0, color=GRAY, ls="--", lw=1)
        bottom.set(xlim=(0, limit), ylim=(-residual_limit, residual_limit), xlabel="Pronosticado (µg/m³)")
    axes[0, 0].set_ylabel("Pronosticado (µg/m³)")
    axes[1, 0].set_ylabel("Residuo (µg/m³)")
    save(fig, output, "ajuste_y_residuos")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelos", type=Path, default=Path("modelos"))
    parser.add_argument("--resultados", type=Path, default=Path("resultados/pronostico"))
    args = parser.parse_args()
    output = args.resultados / "figuras"
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False,
                         "axes.spines.right": False, "axes.labelsize": 10, "font.size": 10})
    model, _ = load_bundle(None, args.modelos, 1)
    rows = pd.read_parquet(args.resultados / "predicciones.parquet")
    evidence = {"arbol": tree_figure(model, output), "episodio": episode_figure(rows, output)}
    lstm_figure(output)
    diagnostics(rows, output)
    (output / "ejemplos.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
