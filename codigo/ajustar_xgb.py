"""Ajusta el pronóstico de una hora y evalúa la receta cerrada en 2025."""

import argparse
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .analizar_mae import errors
from .evaluar import TEST_START, bootstrap_delta, windows
from .evaluar_final import high_mask, training_thresholds
from .modelos_finales import HOLDOUT_START, TUNED_VERSION, VERSION
from .modelos_finales import features, load_bundle, load_sources, predict, save_bundle, train_xgb
from .preparar_lecturas_recientes import Q1_END, Q1_FILE, Q1_START, load_clean
from .pronosticar import CURRENT, TARGET, TARGET_TIME, alert_diagnostics, load_hourly_sources
from .validar_extension import extension_meteorology
from .viento import KEY, STATION, TIME, WIND_FILE, validate_hours


@dataclass(frozen=True)
class Settings:
    max_depth: int = 3
    learning_rate: float = .07
    n_estimators: int = 150
    peso_altos: int = 3


def fit(rows, config, jobs, version=VERSION):
    parameters = asdict(config)
    weight = parameters.pop("peso_altos")
    thresholds = training_thresholds(rows)
    weights = np.where(high_mask(rows, thresholds), weight, 1).astype(np.float32) if weight > 1 else None
    model, state = train_xgb(rows, jobs, weights, parameters=parameters, version=version)
    return model, state | {
        "peso_altos": weight, "ultimo_objetivo_entrenamiento": rows[TARGET_TIME].max().isoformat(),
        "estaciones_entrenamiento": sorted(thresholds.index.tolist()),
    }


def scores(rows, estimate, thresholds):
    alerts = alert_diagnostics(rows, estimate, thresholds)
    del alerts["sesgo_altos_pronosticado_menos_real"]
    return errors(rows, estimate, thresholds) | alerts


def station_scores(rows, estimate, thresholds):
    return [{STATION: st, **scores(group, estimate[rows.index.get_indexer(group.index)], thresholds)}
            for st, group in rows.groupby(STATION)]


def chronological_cv(development, output, jobs):
    folds = [(year, train, valid, training_thresholds(train)) for year, train, valid in windows(development)]
    control = Settings()
    cache = {}
    details, by_station, summaries = [], [], []
    reference = []

    for year, _, rows, thresholds in folds:
        pred = rows[CURRENT].to_numpy()
        reference.append({"anio": year, **scores(rows, pred, thresholds)})

    def evaluate(stage, configs):
        stage_scores = []
        for config in configs:
            if config not in cache:
                records, stations = [], []
                for year, train, valid, thresholds in folds:
                    pred = predict(None, fit(train, config, jobs), valid)
                    records.append({"anio": year, "n_entrenamiento": len(train), **scores(valid, pred, thresholds)})
                    stations.extend({"anio": year, **row} for row in station_scores(valid, pred, thresholds))
                cache[config] = records, stations
                print(f"{stage} · {config}", flush=True)
            records, stations = cache[config]
            label = {"etapa": stage, **asdict(config)}
            details.extend(label | row for row in records)
            by_station.extend(label | row for row in stations)
            mean = {metric: float(np.mean([row[metric] for row in records]))
                    for metric in ("MAE_macro", "MAE", "RMSE", "MAE_altos", "sesgo_altos")}
            stage_scores.append((config, mean))
            summaries.append(label | mean)
        return stage_scores

    def choose(results, max_peak_mae):
        eligible = [(c, m) for c, m in results if m["MAE_altos"] <= max_peak_mae]
        if not eligible:
            return control
        return min(eligible, key=lambda item: (item[1]["MAE_macro"], item[0].n_estimators, item[0].peso_altos))[0]

    depths = evaluate("profundidad", [replace(control, max_depth=depth) for depth in (2, 3, 5)])
    reference_metrics = dict(depths)[control]
    grid = evaluate("tasa_arboles", [Settings(5, rate, trees) for rate in (.03, .07, .12) for trees in (100, 150, 300)])
    # La rejilla incluye la combinación de profundidad 5 ya validada frente al control.
    tuned = choose(grid, reference_metrics["MAE_altos"])
    weights = evaluate("peso_altos", [replace(tuned, peso_altos=weight) for weight in (1, 3, 5)])
    selected = choose(weights, reference_metrics["MAE_altos"])

    output.mkdir(parents=True, exist_ok=True)
    for name, items in (("pliegues", details), ("cv_por_estacion", by_station),
                        ("validacion", summaries), ("persistencia_cv", reference)):
        pd.DataFrame(items).to_csv(output / f"{name}.csv", index=False, float_format="%.9g")
    plot_grid(pd.DataFrame(summaries), tuned, output / "rejilla.png")
    selection = {"control": asdict(control), "candidato": asdict(selected), "rejilla": asdict(tuned),
                 "pliegues": [year for year, *_ in folds],
                 "criterio": "Menor MAE macro con MAE en altos igual o menor al del control",
                 "corte_entrenamiento_ampliado": HOLDOUT_START.isoformat()}
    write_json(output / "seleccion.json", selection)
    print(json.dumps(selection, ensure_ascii=False), flush=True)


def plot_grid(summary, chosen, destination):
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    grid = summary.loc[summary.etapa.eq("tasa_arboles")].pivot(
        index="learning_rate", columns="n_estimators", values="MAE_macro",
    )
    fig = Figure(figsize=(6.5, 4.3), layout="constrained")
    axis = fig.subplots()
    axis.imshow(grid.to_numpy(), cmap="Blues_r", aspect="auto")
    axis.set_xticks(range(len(grid.columns)), labels=grid.columns)
    axis.set_yticks(range(len(grid.index)), labels=[str(x).replace(".", ",") for x in grid.index])
    axis.set(xlabel="Número de árboles", ylabel="Tasa de aprendizaje",
             title="MAE por estación en los pliegues 2020–2022 (µg/m³)")
    cutoff = (grid.to_numpy().min() + grid.to_numpy().max()) / 2
    for i, rate in enumerate(grid.index):
        for j, trees in enumerate(grid.columns):
            value = grid.loc[rate, trees]
            axis.text(j, i, f"{value:.3f}".replace(".", ","), ha="center", va="center",
                      color="white" if value < cutoff else "#172e37", weight="bold")
    i, j = grid.index.get_loc(chosen.learning_rate), grid.columns.get_loc(chosen.n_estimators)
    axis.add_patch(Rectangle((j - .47, i - .47), .94, .94, fill=False, edgecolor="#da5b28", linewidth=2.6))
    fig.savefig(destination, dpi=150)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_final_frame(data_dir, wind_path, downloads):
    air, weather, wind = load_sources(data_dir, wind_path, downloads)
    future_air = load_clean(data_dir / Q1_FILE, Q1_START, Q1_END)
    future_weather, future_wind = extension_meteorology(
        downloads, start=str(Q1_START.date()), end=str(Q1_END.date()),
    )
    combined = [pd.concat([old, new[old.columns]], ignore_index=True) for old, new in (
        (air, future_air), (weather, future_weather), (wind, future_wind),
    )]
    for table, name in zip(combined, ("PM2.5", "ERA5", "IFS"), strict=True):
        validate_hours(table, name)
    return features(*combined, 1)


def final_evaluation(frame, output, models, jobs):
    selection = json.loads((output / "seleccion.json").read_text(encoding="utf-8"))
    control, tuned = (Settings(**selection[name]) for name in ("control", "candidato"))
    original = frame.loc[frame[TARGET_TIME].lt(TEST_START)]
    extended = frame.loc[frame[TARGET_TIME].lt(HOLDOUT_START)]
    thresholds = training_thresholds(original)
    cohorts = {
        "oct_dic_2024": frame.loc[frame[TIME].ge(HOLDOUT_START) & frame[TARGET_TIME].lt(Q1_START)],
        "ene_mar_2025": frame.loc[frame[TIME].ge(Q1_START) & frame[TARGET_TIME].le(Q1_END)],
    }
    if any(rows.empty for rows in cohorts.values()):
        raise ValueError("Faltan pares de horas consecutivas en uno de los periodos de prueba")
    first = load_bundle(None, models, 1)
    recompiled = fit(original, control, jobs)
    second = fit(extended, control, jobs)
    third = fit(extended, tuned, jobs, TUNED_VERSION)
    trials = {"A_pm25_1": first, "B_mas_datos": second, "C_ajustado": third}
    metrics, by_station, differences, predictions = [], [], [], []
    reproduction = {}
    for period, rows in cohorts.items():
        estimates = {"persistencia": rows[CURRENT].to_numpy()}
        estimates.update({name: predict(None, bundle, rows) for name, bundle in trials.items()})
        for label, values in estimates.items():
            common = {"periodo": period, "modelo": label}
            metrics.append(common | scores(rows, values, thresholds))
            by_station.extend(common | row for row in station_scores(rows, values, thresholds))
        replay = predict(None, recompiled, rows)
        reproduction[period] = float(np.max(np.abs(replay - estimates["A_pm25_1"])))
        for candidate, reference in (("A_pm25_1", "persistencia"), ("B_mas_datos", "A_pm25_1"),
                                     ("C_ajustado", "B_mas_datos"), ("C_ajustado", "A_pm25_1")):
            differences.append({"periodo": period, "modelo": candidate, "referencia": reference,
                                **bootstrap_delta(rows, estimates[candidate], estimates[reference], target=TARGET)})
        predictions.append(rows[KEY + [TARGET_TIME, CURRENT, TARGET]].assign(
            periodo=period, alta=high_mask(rows, thresholds), **estimates,
        ))

    test = pd.DataFrame(metrics)
    final = test.loc[test.periodo.eq("ene_mar_2025")].set_index("modelo")
    relevant = ["MAE_macro", "MAE_altos"]
    promote = (final.loc["C_ajustado", relevant] <= final.loc["A_pm25_1", relevant]).all() and (
        final.loc["C_ajustado", "MAE_macro"] < final.loc["A_pm25_1", "MAE_macro"]
    )
    recommended = TUNED_VERSION if promote else VERSION
    decision = {"periodo": "ene_mar_2025", "version_recomendada": recommended,
                "candidato": TUNED_VERSION, "criterio": "Reduce MAE macro y no incrementa MAE en altos frente a pm25-1",
                "filas_entrenamiento_original": len(original), "filas_entrenamiento_ampliado": len(extended),
                "reproduccion_max_error_absoluto_ug_m3": reproduction,
                "hiperparametros": third[1]["hiperparametros"], "peso_altos": third[1]["peso_altos"]}
    write_json(output / "decision.json", decision)
    save_bundle(models, third)
    for name, table in (("prueba", test), ("por_estacion", pd.DataFrame(by_station)),
                        ("diferencias", pd.DataFrame(differences)), ("umbrales_altos", thresholds.rename("P95").reset_index())):
        table.to_csv(output / f"{name}.csv", index=False, float_format="%.9g")
    pd.concat(predictions, ignore_index=True).to_parquet(output / "predicciones.parquet", index=False)
    print(test[["periodo", "modelo", "n", "MAE", "MAE_macro", "MAE_altos", "sesgo_altos"]].to_string(index=False))
    print(f"Versión recomendada: {recommended}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=("ajustar", "evaluar"))
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path)
    parser.add_argument("--descargas-clima", type=Path)
    parser.add_argument("--salida", type=Path, default=Path("resultados/ajuste_1h"))
    parser.add_argument("--modelos", type=Path, default=Path("modelos"))
    parser.add_argument("--hilos", type=int, default=4)
    args = parser.parse_args()
    if args.hilos < 1:
        parser.error("--hilos debe ser al menos 1")
    wind_path = args.viento or args.datos / WIND_FILE
    with threadpool_limits(limits=args.hilos):
        if args.accion == "ajustar":
            sources = load_hourly_sources(args.datos, wind_path, False, None)
            development = features(*(rows.loc[rows[TIME].lt(TEST_START)] for rows in sources), 1)
            chronological_cv(development, args.salida, args.hilos)
        else:
            frame = load_final_frame(args.datos, wind_path, args.descargas_clima)
            final_evaluation(frame, args.salida, args.modelos, args.hilos)


if __name__ == "__main__":
    main()
