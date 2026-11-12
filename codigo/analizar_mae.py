"""Contrasta nuevos rezagos, capa límite y modelos sobre las mismas horas de PM2.5."""

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .actualizar_aire import EXTENSION_START
from .evaluar import TEST_START, bootstrap_delta, score, windows
from .pronosticar import CURRENT, GROUPS, HORIZONS, TARGET, TARGET_TIME, Candidate
from .pronosticar import build_matrix, candidates, fit_and_predict, load_hourly_sources, station_matrix
from .viento import KEY, SPEED, STATION, STATIONS, TIME, WIND_FILE, validate_hours


LAYER = "boundary_layer_height"
CLIMATE = [LAYER, "ventilacion_m2_s"]
HISTORY = ["pm_lag_2h", "pm_lag_12h", "pm_lag_48h", "pm_lag_168h", "pm_mediana_3h",
           "pm_desviacion_24h", "pm_cambio_1h", "pm_hora_objetivo_ayer", "pm_hora_objetivo_semana",
           "pm_hora_objetivo_media_3dias", "dia_semana_seno", "dia_semana_coseno"]
GROUP_BY_HOUR = {1: "vecinos", 3: "viento", 6: "local"}
BASELINE = "referencia_previa"


def historical_features(air, horizon):
    calendar = pd.date_range(air[TIME].min(), air[TIME].max(), freq="h")
    concentration = station_matrix(air, CURRENT, calendar)
    weekly_angle = 2 * np.pi * calendar.dayofweek / 7
    frames = []
    for station in STATIONS:
        pm = concentration[station]
        past_target = pd.concat([pm.shift(day * 24 - horizon) for day in (1, 2, 3)], axis=1)
        features = pd.DataFrame({STATION: station, TIME: calendar})
        for lag in (2, 12, 48, 168):
            features[f"pm_lag_{lag}h"] = pm.shift(lag).to_numpy()
        features["pm_mediana_3h"] = pm.rolling(3, min_periods=2).median().to_numpy()
        features["pm_desviacion_24h"] = pm.rolling(24, min_periods=12).std().to_numpy()
        features["pm_cambio_1h"] = pm.diff().to_numpy()
        features["pm_hora_objetivo_ayer"] = past_target.iloc[:, 0].to_numpy()
        features["pm_hora_objetivo_semana"] = pm.shift(168 - horizon).to_numpy()
        features["pm_hora_objetivo_media_3dias"] = past_target.mean(axis=1).where(past_target.count(axis=1).ge(2)).to_numpy()
        features["dia_semana_seno"], features["dia_semana_coseno"] = np.sin(weekly_angle), np.cos(weekly_angle)
        frames.append(features)
    return pd.concat(frames, ignore_index=True)


def forecasts(horizon, train, evaluations, jobs):
    specs = {spec.name: spec for spec in candidates()}
    group = GROUP_BY_HOUR[horizon]
    xgb = specs[f"xgb_{group}"]
    columns = GROUPS[group]
    histgb = specs["histgb_vecinos_15"]
    result = {"persistencia": [rows[CURRENT].to_numpy() for rows in evaluations]}
    result["xgb_control"] = fit_and_predict(xgb, train, evaluations, jobs)
    result[BASELINE] = result["xgb_control"]
    if horizon == 1:
        reference = fit_and_predict(histgb, train, evaluations, jobs)
        result[BASELINE] = [(x + h) / 2 for x, h in zip(result["xgb_control"], reference)]

    for label, candidate in (
        ("xgb_historia", xgb),
        ("xgb_historia_mae", replace(xgb, parameters=xgb.parameters | {"objective": "reg:absoluteerror"})),
        ("histgb_historia", histgb),
        ("extratrees_historia", Candidate("extratrees_historia", "ExtraTrees", group,
                                         dict(n_estimators=48, max_depth=16, min_samples_leaf=15,
                                              max_features=.75, bootstrap=True, max_samples=.45))),
    ):
        result[label] = fit_and_predict(candidate, train, evaluations, jobs, columns + HISTORY)

    # Donde falta la altura, se usa el XGBoost equivalente entrenado sin ese grupo.
    available_train = train.loc[train[LAYER].notna()]
    if available_train.empty:
        raise ValueError("Falta altura de capa límite en el entrenamiento")
    for label, extra, fallback in (("xgb_capa", [], "xgb_control"),
                                  ("xgb_historia_capa", HISTORY, "xgb_historia")):
        available_eval = [rows[LAYER].notna().to_numpy() for rows in evaluations]
        pairs = [(i, rows.loc[mask]) for i, (rows, mask) in enumerate(zip(evaluations, available_eval)) if mask.any()]
        result[label] = [p.copy() for p in result[fallback]]
        if pairs:
            predicted = fit_and_predict(xgb, available_train, [rows for _, rows in pairs], jobs,
                                        columns + extra + CLIMATE)
            for (i, _), values in zip(pairs, predicted):
                result[label][i][available_eval[i]] = values
    return result


def errors(rows, pred, high_thresholds):
    actual = rows[TARGET].to_numpy()
    high = actual >= rows[STATION].map(high_thresholds).to_numpy()
    error = pred - actual
    return {**score(rows, pred, TARGET), "n_altos": int(high.sum()),
            "MAE_altos": np.abs(error[high]).mean() if high.any() else np.nan,
            "sesgo_altos": error[high].mean() if high.any() else np.nan}


def compare(frame, horizon, jobs):
    dev = frame.loc[frame[TARGET_TIME].lt(TEST_START)]
    history = frame.loc[frame[TIME].ge(TEST_START) & frame[TARGET_TIME].lt(EXTENSION_START)]
    later = frame.loc[frame[TIME].ge(EXTENSION_START)]
    cohorts = {"historica": history}
    if not later.empty:
        cohorts["openaq_2024"] = later
    folds = []
    for year, train, valid in windows(dev):
        comparison = forecasts(horizon, train, [valid], jobs)
        folds.extend({"anio": year, "modelo": name, **score(valid, pred[0], TARGET)} for name, pred in comparison.items())
        print(f"+{horizon} h · validación {year} terminada", flush=True)
    folds = pd.DataFrame(folds)
    validation = folds.groupby("modelo", as_index=False).agg(
        MAE_macro_cv=("MAE_macro", "mean"), DE_MAE_macro_cv=("MAE_macro", "std"),
    ).sort_values(["MAE_macro_cv", "modelo"], ignore_index=True)
    selected = validation.iloc[0].modelo
    predictions = forecasts(horizon, dev, list(cohorts.values()), jobs)
    highs = dev.groupby(STATION)[TARGET].quantile(.95)
    metrics, stations, intervals = [], [], []
    for i, (period, rows) in enumerate(cohorts.items()):
        for subset, data in (("todas", rows), ("capa_disponible", rows.loc[rows[LAYER].notna()])):
            if data.empty:
                continue
            idx = rows.index.get_indexer(data.index)
            for name, pred in predictions.items():
                metrics.append({"periodo": period, "subconjunto": subset, "modelo": name,
                                "proporcion_capa_disponible": data[LAYER].notna().mean(),
                                **errors(data, pred[i][idx], highs)})
            paired = [("xgb_historia", "xgb_control"), ("xgb_capa", "xgb_control"),
                      ("xgb_historia_capa", "xgb_historia"), ("xgb_historia_mae", "xgb_historia")]
            if selected != BASELINE:
                paired.append((selected, BASELINE))
            for name, reference in dict.fromkeys(paired):
                effect = bootstrap_delta(data, predictions[name][i][idx], predictions[reference][i][idx], target=TARGET)
                intervals.append({"periodo": period, "subconjunto": subset, "modelo": name,
                                  "referencia": reference, "n": len(data), **effect})
        for station, data in rows.groupby(STATION):
            idx = rows.index.get_indexer(data.index)
            for name in dict.fromkeys(("persistencia", BASELINE, selected)):
                stations.append({"periodo": period, STATION: station, "modelo": name,
                                 **errors(data, predictions[name][i][idx], highs)})
    return {"validacion": validation.assign(seleccionado=validation.modelo.eq(selected)), "pliegues": folds,
            "prueba": pd.DataFrame(metrics), "por_estacion": pd.DataFrame(stations), "diferencias": pd.DataFrame(intervals)}


def run(data_dir, wind_path, output, horizons, jobs, extension=False, downloads=None):
    air, weather, wind = load_hourly_sources(data_dir, wind_path, extension, downloads)
    layer = pd.read_parquet(data_dir / "clima_era5_7_estaciones.parquet", columns=KEY + [LAYER])
    validate_hours(layer, "altura de capa límite ERA5")
    layer[LAYER] = layer[LAYER].where(np.isfinite(layer[LAYER]) & layer[LAYER].gt(0))
    collected = {}
    with threadpool_limits(limits=jobs):
        for horizon in horizons:
            frame = build_matrix(air, weather, wind, horizon).merge(layer, on=KEY, how="left", validate="one_to_one")
            frame = frame.merge(historical_features(air, horizon), on=KEY, how="left", validate="one_to_one")
            frame["ventilacion_m2_s"] = frame[LAYER] * frame[SPEED]
            for name, table in compare(frame, horizon, jobs).items():
                collected.setdefault(name, []).append(table.assign(horizonte_h=horizon))
    output.mkdir(parents=True, exist_ok=True)
    for name, tables in collected.items():
        pd.concat(tables, ignore_index=True).to_csv(output / f"{name}.csv", index=False, float_format="%.9g")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path)
    parser.add_argument("--salida", type=Path, default=Path("resultados/analisis_mae"))
    parser.add_argument("--horizontes", type=int, choices=HORIZONS, nargs="+", default=HORIZONS)
    parser.add_argument("--hilos", type=int, default=4)
    parser.add_argument("--extender", action="store_true")
    parser.add_argument("--descargas-clima", type=Path)
    args = parser.parse_args()
    if args.hilos < 1:
        parser.error("--hilos debe ser al menos 1")
    try:
        run(args.datos, args.viento or args.datos / WIND_FILE, args.salida, dict.fromkeys(args.horizontes),
            args.hilos, args.extender, args.descargas_clima)
    except FileNotFoundError as error:
        parser.error(f"Falta un insumo: {error.filename or error}. Revisa --datos y --viento.")


if __name__ == "__main__":
    main()
