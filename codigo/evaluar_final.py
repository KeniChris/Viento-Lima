"""Contrasta la ponderación de episodios altos y evalúa los modelos en un periodo reservado."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .analizar_mae import errors
from .evaluar import TEST_START, bootstrap_delta, windows
from .evaluar_lstm import histories, make_sequences
from .modelos_finales import HOLDOUT_START, MODELS, VERSION, configure_tensorflow, features
from .modelos_finales import load_sources, predict, save_bundle, train_model
from .pronosticar import CURRENT, TARGET, TARGET_TIME
from .viento import KEY, STATION, TIME, WIND_FILE


HIGH_WEIGHT = 3


def training_thresholds(train):
    return train.groupby(STATION)[TARGET].quantile(.95)


def high_mask(rows, thresholds):
    return rows[TARGET].ge(rows[STATION].map(thresholds)).to_numpy()


def forecast(tf, bundle, rows, calendar, grid):
    sequence = histories(rows, calendar, grid) if bundle[1]["horizonte_h"] != 1 else None
    return predict(tf, bundle, rows, sequence)


def compare_weights(tf, development, calendar, grid, horizon, jobs):
    records = []
    for year, train, valid in windows(development):
        thresholds = training_thresholds(train)
        for weight in (1, HIGH_WEIGHT):
            sample_weight = np.where(high_mask(train, thresholds), weight, 1).astype(np.float32) if weight > 1 else None
            bundle = train_model(tf, train, calendar, grid, horizon, jobs, sample_weight)
            pred = forecast(tf, bundle, valid, calendar, grid)
            records.append({"horizonte_h": horizon, "anio": year, "peso_altos": weight,
                            **errors(valid, pred, thresholds)})
        print(f"{horizon} h · validación {year}", flush=True)
    folds = pd.DataFrame(records)
    means = folds.groupby("peso_altos").agg(
        MAE_macro_cv=("MAE_macro", "mean"), MAE_altos_cv=("MAE_altos", "mean"),
        sesgo_altos_cv=("sesgo_altos", "mean"),
    )
    better = means.loc[HIGH_WEIGHT, ["MAE_macro_cv", "MAE_altos_cv"]].lt(
        means.loc[1, ["MAE_macro_cv", "MAE_altos_cv"]],
    ).all()
    weight = HIGH_WEIGHT if better else 1
    return folds, means.reset_index().assign(horizonte_h=horizon, seleccionado=lambda m: m.peso_altos.eq(weight)), weight


def evaluate_holdout(rows, estimated, thresholds, horizon):
    baseline = rows[CURRENT].to_numpy()
    comparisons = {"persistencia": baseline, MODELS[horizon]: estimated}
    results, stations = [], []
    for name, prediction in comparisons.items():
        results.append({"horizonte_h": horizon, "modelo": name, **errors(rows, prediction, thresholds)})
        for station, group in rows.groupby(STATION):
            position = rows.index.get_indexer(group.index)
            stations.append({"horizonte_h": horizon, STATION: station, "modelo": name,
                             **errors(group, prediction[position], thresholds)})
    comparison = bootstrap_delta(rows, estimated, baseline, target=TARGET)
    return (pd.DataFrame(results), pd.DataFrame(stations),
            pd.DataFrame([{"horizonte_h": horizon, **comparison}]))


def run(tf, air, weather, wind, output, models, horizons, jobs):
    output.mkdir(parents=True, exist_ok=True)
    calendar, grid = make_sequences(air)
    tables, predictions, selections = {}, [], []
    for horizon in horizons:
        frame = features(air, weather, wind, horizon)
        development = frame.loc[frame[TARGET_TIME].lt(TEST_START)]
        evaluation = frame.loc[frame[TIME].ge(HOLDOUT_START)]
        folds, summary, weight = compare_weights(tf, development, calendar, grid, horizon, jobs)
        thresholds = training_thresholds(development)
        weights = np.where(high_mask(development, thresholds), weight, 1).astype(np.float32) if weight > 1 else None
        bundle = train_model(tf, development, calendar, grid, horizon, jobs, weights)
        bundle[1]["peso_altos"] = weight
        save_bundle(models, bundle)
        pred = forecast(tf, bundle, evaluation, calendar, grid)
        results, stations, intervals = evaluate_holdout(evaluation, pred, thresholds, horizon)
        for name, table in (("pliegues_pesos", folds), ("validacion_pesos", summary), ("prueba", results),
                            ("por_estacion", stations), ("diferencia_persistencia", intervals)):
            tables.setdefault(name, []).append(table)
        result = evaluation[KEY + [TARGET_TIME, CURRENT, TARGET]].assign(
            horizonte_h=horizon, modelo=MODELS[horizon], pm25_estimado=pred, alta=high_mask(evaluation, thresholds),
        )
        predictions.append(result)
        selections.append({"horizonte_h": horizon, "modelo": MODELS[horizon], "version": VERSION, "peso_altos": weight})
        print(f"{horizon} h · prueba independiente · MAE {results.iloc[1].MAE:.4f} · peso de episodios {weight}", flush=True)

    for name, pieces in tables.items():
        pd.concat(pieces, ignore_index=True).to_csv(output / f"{name}.csv", index=False, float_format="%.9g")
    pd.concat(predictions, ignore_index=True).to_parquet(output / "predicciones.parquet", index=False)
    (output / "modelos.json").write_text(json.dumps(selections, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path)
    parser.add_argument("--descargas-clima", type=Path)
    parser.add_argument("--salida", type=Path, default=Path("resultados/pronostico"))
    parser.add_argument("--modelos", type=Path, default=Path("modelos"))
    parser.add_argument("--horizontes", type=int, choices=MODELS, nargs="+", default=list(MODELS))
    parser.add_argument("--hilos", type=int, default=3)
    args = parser.parse_args()
    if args.hilos < 1:
        parser.error("--hilos debe ser al menos 1")
    tf = configure_tensorflow(args.hilos)
    sources = load_sources(args.datos, args.viento or args.datos / WIND_FILE, args.descargas_clima)
    with threadpool_limits(limits=args.hilos):
        run(tf, *sources, args.salida, args.modelos, dict.fromkeys(args.horizontes), args.hilos)


if __name__ == "__main__":
    main()
