"""Compara pronósticos horarios de PM2.5 y el aporte del viento entre estaciones."""

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits
from xgboost import XGBRegressor

from .actualizar_aire import EXTENSION_START
from .evaluar import SEED, TEST_START, bootstrap_delta, score, windows
from .focos import FIRE_ALIGNED, FIRE_NEAR, fire_features, load_detections
from .preparar import CURRENT, HOUR, METEO, TARGET_TIME, load_air, max_observed, station_geometry
from .preparar import weighted_average, wind_components
from .validar_extension import extended_air, extension_meteorology
from .viento import CALM_MS, DIRECTION, KEY, SPEED, STATION, STATIONS, TIME, WIND_FILE, load_wind, validate_hours


TARGET = "pm25_objetivo"
START = pd.Timestamp("2017-01-01")
HORIZONS = (1, 3, 6)
LAGS = range(7)
LOCAL = [STATION, CURRENT, "pm_lag_1h", "pm_lag_3h", "pm_lag_6h", "pm_lag_24h",
         "pm_media_6h", "pm_std_6h", "pm_media_24h", "pm_cambio_3h",
         "hora_seno", "hora_coseno", "mes_seno", "mes_coseno", "fin_de_semana", *METEO[:4]]
WIND = [SPEED, "viento_este_ms", "viento_norte_ms"]
NEIGHBORS = [f"vecinos_{metric}_{lag}h" for lag in LAGS for metric in ("media", "max", "alza")]
TRANSPORT = ["transporte_pm_media", "transporte_alza_media", "transporte_alza_max",
             "transporte_origenes", "transporte_peso"]
GROUPS = {
    "local": LOCAL,
    "viento": LOCAL + WIND,
    "vecinos": LOCAL + WIND + NEIGHBORS,
    "transporte": LOCAL + WIND + NEIGHBORS + TRANSPORT,
}
STATION_GROUPS = tuple(GROUPS)
GROUPS["focos_cercanos"] = GROUPS["transporte"] + FIRE_NEAR
GROUPS["focos_alineados"] = GROUPS["focos_cercanos"] + FIRE_ALIGNED
ABLATION = tuple(f"xgb_{group}" for group in STATION_GROUPS)
FIRE_ABLATION = ("xgb_transporte", "xgb_focos_cercanos", "xgb_focos_alineados")
ENSEMBLE = "promedio_xgb_histgb"


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    group: str
    parameters: dict


def candidates(include_fires=False):
    xgb = dict(n_estimators=150, max_depth=3, learning_rate=.07, min_child_weight=20,
               subsample=.8, colsample_bytree=.8, reg_lambda=8)
    histgb = dict(max_iter=120, learning_rate=.07, min_samples_leaf=45, l2_regularization=5)
    groups = GROUPS if include_fires else STATION_GROUPS
    return (
        *(Candidate(f"xgb_{group}", "XGBoost", group, xgb) for group in groups),
        Candidate("xgb_vecinos_d5", "XGBoost", "vecinos", xgb | {"max_depth": 5}),
        Candidate("histgb_vecinos_15", "HistGradientBoosting", "vecinos", histgb | {"max_leaf_nodes": 15}),
        Candidate("histgb_vecinos_31", "HistGradientBoosting", "vecinos", histgb | {"max_leaf_nodes": 31}),
        Candidate("ridge_vecinos_1", "Ridge", "vecinos", {"alpha": 1}),
        Candidate("ridge_vecinos_100", "Ridge", "vecinos", {"alpha": 100}),
    )


def make_regressor(spec, jobs, columns=None):
    columns = GROUPS[spec.group] if columns is None else columns
    numeric = [column for column in columns if column != STATION]
    numeric_steps = [SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)]
    if spec.family == "Ridge":
        numeric_steps.append(StandardScaler())
        model = Ridge(**spec.parameters)
    elif spec.family == "XGBoost":
        model = XGBRegressor(tree_method="hist", n_jobs=jobs, random_state=SEED, **spec.parameters)
    elif spec.family == "ExtraTrees":
        model = ExtraTreesRegressor(n_jobs=jobs, random_state=SEED, **spec.parameters)
    else:
        model = HistGradientBoostingRegressor(early_stopping=False, random_state=SEED, **spec.parameters)
    transform = ColumnTransformer([
        ("numericas", make_pipeline(*numeric_steps), numeric),
        ("estacion", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [STATION]),
    ])
    return make_pipeline(transform, model)


def fit_and_predict(spec, train, evaluations, jobs, columns=None):
    columns = GROUPS[spec.group] if columns is None else columns
    change = train[TARGET] - train[CURRENT]
    model = make_regressor(spec, jobs, columns).fit(train[columns], change)
    return [np.maximum(0, rows[CURRENT].to_numpy() + model.predict(rows[columns])) for rows in evaluations]


def station_matrix(frame, column, calendar):
    return frame.pivot(index=TIME, columns=STATION, values=column).reindex(index=calendar, columns=STATIONS)


def arrival_features(levels, rises, speed, u, v, origin_indices, geometry, horizon):
    # Fila de la matriz de pesos = rezago de la lectura. Solo se usan horas <= t.
    shape = (len(origin_indices), levels.shape[1])
    weights, pm, increase = np.zeros(shape), np.full(shape, np.nan), np.full(shape, np.nan)
    wind_available = np.isfinite(speed[:, :, origin_indices]).any(axis=(0, 2))
    elapsed = np.asarray(LAGS)[:, None] + horizon
    for index, (origin, (distance, east, north)) in enumerate(zip(origin_indices, geometry)):
        along = u[:, :, origin] * east + v[:, :, origin] * north
        alignment = along / np.maximum(speed[:, :, origin], CALM_MS)
        distance_error = (along * 3.6 * elapsed - distance) / max(2.5, .35 * distance)
        observed = levels[:, :, origin]
        eligible = (alignment >= np.cos(np.pi / 4)) & (np.abs(distance_error) <= 2) & np.isfinite(observed)
        weight = np.where(eligible, alignment * np.exp(-.5 * distance_error**2) / distance, 0)
        best = weight.argmax(axis=0)[None, :]
        weights[index] = np.take_along_axis(weight, best, axis=0)[0]
        pm[index] = np.take_along_axis(observed, best, axis=0)[0]
        increase[index] = np.take_along_axis(rises[:, :, origin], best, axis=0)[0]
    usable_rise = (weights > 0) & np.isfinite(increase)
    return {
        "transporte_pm_media": weighted_average(pm, weights),
        "transporte_alza_media": weighted_average(increase, np.where(usable_rise, weights, 0)),
        "transporte_alza_max": max_observed(increase, usable_rise),
        "transporte_origenes": np.where(wind_available, (weights > 0).sum(axis=0), np.nan),
        "transporte_peso": np.where(wind_available, weights.sum(axis=0), np.nan),
    }


def build_matrix(air, weather, wind, horizon):
    calendar = pd.date_range(air[TIME].min(), air[TIME].max(), freq="h")
    concentration = station_matrix(air, CURRENT, calendar)
    speed = station_matrix(wind, SPEED, calendar)
    direction = station_matrix(wind, DIRECTION, calendar)
    u, v = wind_components(speed.where(speed.ge(CALM_MS)), direction)
    rise = (concentration - concentration.shift(3)).clip(lower=0)

    def history(matrix):
        return np.stack([matrix.shift(lag).to_numpy() for lag in LAGS])

    levels, rises = history(concentration), history(rise)
    speed_history, u_history, v_history = history(speed), history(u), history(v)
    target_time = calendar + horizon * HOUR
    frames = []
    for destination, receiver in enumerate(STATIONS):
        series = concentration[receiver]
        row = pd.DataFrame({STATION: receiver, TIME: calendar, TARGET_TIME: target_time, CURRENT: series.to_numpy()})
        row[TARGET] = series.shift(-horizon).to_numpy()
        for lag in (1, 3, 6, 24):
            row[f"pm_lag_{lag}h"] = series.shift(lag).to_numpy()
        row["pm_media_6h"] = series.rolling(6, min_periods=4).mean().to_numpy()
        row["pm_std_6h"] = series.rolling(6, min_periods=4).std().to_numpy()
        row["pm_media_24h"] = series.rolling(24, min_periods=12).mean().to_numpy()
        row["pm_cambio_3h"] = row[CURRENT] - row["pm_lag_3h"]
        hour_angle = 2 * np.pi * target_time.hour / 24
        month_angle = 2 * np.pi * target_time.month / 12
        row["hora_seno"], row["hora_coseno"] = np.sin(hour_angle), np.cos(hour_angle)
        row["mes_seno"], row["mes_coseno"] = np.sin(month_angle), np.cos(month_angle)
        row["fin_de_semana"] = target_time.dayofweek >= 5
        row[SPEED] = speed[receiver].to_numpy()
        row["viento_este_ms"], row["viento_norte_ms"] = u[receiver].to_numpy(), v[receiver].to_numpy()
        origins = [origin for origin in range(len(STATIONS)) if origin != destination]
        geometry = [station_geometry(concentration.columns[origin], receiver) for origin in origins]
        distance = np.asarray(geometry)[:, 0, None]
        for lag in LAGS:
            values, increases = levels[lag][:, origins].T, rises[lag][:, origins].T
            observed = np.isfinite(values)
            row[f"vecinos_media_{lag}h"] = weighted_average(values, observed / distance)
            row[f"vecinos_max_{lag}h"] = max_observed(values, observed)
            row[f"vecinos_alza_{lag}h"] = max_observed(increases, np.isfinite(increases))
        spatial = arrival_features(levels, rises, speed_history, u_history, v_history, origins, geometry, horizon)
        row = row.assign(**spatial)
        frames.append(row.loc[row[TIME].ge(START)].dropna(subset=[CURRENT, TARGET]))
    frame = pd.concat(frames, ignore_index=True).merge(weather, on=KEY, how="left", validate="one_to_one")
    return frame.sort_values([TIME, STATION], ignore_index=True)


def alert_diagnostics(rows, prediction, thresholds):
    observed = rows[TARGET].to_numpy()
    threshold = rows[STATION].map(thresholds).to_numpy()
    below_at_issue = rows[CURRENT].to_numpy() < threshold
    high, forecast_high = observed >= threshold, prediction >= threshold
    crossing = below_at_issue & high
    alarm = below_at_issue & forecast_high
    hits = int((crossing & alarm).sum())
    return {
        "n_altos": int(high.sum()),
        "sesgo_altos_pronosticado_menos_real": (prediction[high] - observed[high]).mean() if high.any() else np.nan,
        "recall_altos": (high & forecast_high).sum() / high.sum() if high.any() else np.nan,
        "n_cruces": int(crossing.sum()), "n_alertas_cruce": int(alarm.sum()), "aciertos_cruce": hits,
        "recall_cruces": hits / crossing.sum() if crossing.any() else np.nan,
        "precision_cruces": hits / alarm.sum() if alarm.any() else np.nan,
    }


def residual_bins(rows, prediction):
    observed = rows[TARGET].to_numpy()
    pairs = pd.DataFrame({"pronosticado": prediction, "observado": observed, "residuo": observed - prediction})
    bins = pd.cut(prediction, bins=[0, 10, 20, 30, 40, 50, 60, 80, np.inf], right=False)
    return pairs.groupby(bins, observed=True).agg(
        n=("residuo", "size"), pronosticado_mediana=("pronosticado", "median"),
        observado_mediana=("observado", "median"), residuo_mediana=("residuo", "median"),
        residuo_p10=("residuo", lambda s: s.quantile(.1)), residuo_p90=("residuo", lambda s: s.quantile(.9)),
    ).rename_axis("rango_pronosticado").reset_index()


def compare_horizon(frame, horizon, jobs, include_fires):
    specs = {spec.name: spec for spec in candidates(include_fires)}
    ablation = ABLATION + FIRE_ABLATION[1:] if include_fires else ABLATION
    dev = frame.loc[frame[TARGET_TIME].lt(TEST_START)]
    historical = frame.loc[frame[TIME].ge(TEST_START) & frame[TARGET_TIME].lt(EXTENSION_START)]
    extension = frame.loc[frame[TIME].ge(EXTENSION_START)]
    cohorts = {"historica": historical}
    if not extension.empty:
        cohorts["openaq_2024"] = extension
    folds = []
    for year, train, validation in windows(dev):
        predicted = {"persistencia": validation[CURRENT].to_numpy()}
        for name, spec in specs.items():
            predicted[name] = fit_and_predict(spec, train, [validation], jobs)[0]
        predicted[ENSEMBLE] = (predicted["xgb_vecinos"] + predicted["histgb_vecinos_15"]) / 2
        folds.extend({"anio": year, "modelo": name, "n_entrenamiento": len(train), **score(validation, p, TARGET)}
                     for name, p in predicted.items())
        print(f"+{horizon} h · validación {year} · {len(validation):,} objetivos", flush=True)
    cv = pd.DataFrame(folds)
    summary = cv.groupby("modelo", as_index=False).agg(
        MAE_macro_cv=("MAE_macro", "mean"), DE_MAE_macro_cv=("MAE_macro", "std"), RMSE_cv=("RMSE", "mean"),
    ).sort_values(["MAE_macro_cv", "modelo"], ignore_index=True)
    families = {name: spec.family for name, spec in specs.items()} | {"persistencia": "Referencia", ENSEMBLE: "Ensamble"}
    summary["familia"] = summary.modelo.map(families)
    winner = summary.iloc[0].modelo
    finalists = list(dict.fromkeys([*ablation, *summary.drop_duplicates("familia").modelo, winner]))
    predictions = {"persistencia": [rows[CURRENT].to_numpy() for rows in cohorts.values()]}
    for name in finalists:
        if name in specs:
            predictions[name] = fit_and_predict(specs[name], dev, cohorts.values(), jobs)
    if ENSEMBLE in finalists:
        hist = predictions.get("histgb_vecinos_15")
        if hist is None:
            hist = fit_and_predict(specs["histgb_vecinos_15"], dev, cohorts.values(), jobs)
        predictions[ENSEMBLE] = [(x + h) / 2 for x, h in zip(predictions["xgb_vecinos"], hist)]

    thresholds = dev.groupby(STATION)[TARGET].quantile(.95)
    surge = dev.loc[dev.transporte_alza_max.gt(0), "transporte_alza_max"].quantile(.90)
    metrics, station_metrics, differences, residuals = [], [], [], []
    for i, (cohort, rows) in enumerate(cohorts.items()):
        intense = rows.transporte_alza_max.ge(surge)
        groups = {"todas": rows, "alza_origen_alineada": rows.loc[intense]}
        if include_fires:
            groups["con_focos_cercanos"] = rows.loc[rows[FIRE_NEAR[0]].gt(0)]
            groups["con_focos_alineados"] = rows.loc[rows[FIRE_ALIGNED[0]].gt(0)]
        for subset, group in groups.items():
            if group.empty:
                continue
            where = rows.index.get_indexer(group.index)
            for name, arrays in predictions.items():
                if subset != "todas" and name not in (*ablation, winner, "persistencia"):
                    continue
                pred = arrays[i][where]
                metrics.append({"periodo": cohort, "subconjunto": subset, "modelo": name,
                                **score(group, pred, TARGET), **alert_diagnostics(group, pred, thresholds)})
            for model, reference in zip(ablation[1:], ablation):
                difference = bootstrap_delta(group, predictions[model][i][where], predictions[reference][i][where], target=TARGET)
                differences.append({"periodo": cohort, "subconjunto": subset, "modelo": model,
                                    "referencia": reference, "n": len(group), **difference})
        residuals.append(residual_bins(rows, predictions[winner][i]).assign(periodo=cohort, modelo=winner))
        for station, group in rows.groupby(STATION):
            where = rows.index.get_indexer(group.index)
            for name in dict.fromkeys((winner, "persistencia")):
                pred = predictions[name][i][where]
                station_metrics.append({"periodo": cohort, STATION: station, "modelo": name,
                                        **score(group, pred, TARGET), **alert_diagnostics(group, pred, thresholds)})
    return {
        "validacion": summary.assign(seleccionado=summary.modelo.eq(winner)), "pliegues": cv,
        "prueba": pd.DataFrame(metrics), "por_estacion": pd.DataFrame(station_metrics),
        "ablacion": pd.DataFrame(differences), "residuos": pd.concat(residuals, ignore_index=True),
        "umbrales": thresholds.rename("p95_pm25").rename_axis(STATION).reset_index().assign(p90_alza_origen=surge),
    }


def load_hourly_sources(data_dir, wind_path, include_extension, downloads):
    air, _ = load_air(data_dir)
    weather = pd.read_parquet(data_dir / "clima_era5_7_estaciones.parquet", columns=KEY + METEO[:4])
    wind, _ = load_wind(wind_path)
    if include_extension:
        more_air = extended_air(data_dir)
        more_weather, more_wind = extension_meteorology(downloads)
        air = pd.concat([air[KEY + [CURRENT]], more_air.loc[more_air[TIME].ge(EXTENSION_START)]], ignore_index=True)
        weather = pd.concat([weather, more_weather.loc[more_weather[TIME].ge(EXTENSION_START), weather.columns]], ignore_index=True)
        wind = pd.concat([wind, more_wind.loc[more_wind[TIME].ge(EXTENSION_START)]], ignore_index=True)
    validate_hours(air, "PM2.5")
    validate_hours(weather, "ERA5")
    validate_hours(wind, "viento IFS")
    weather["relative_humidity_2m"] = weather.relative_humidity_2m.where(weather.relative_humidity_2m.between(0, 100))
    weather["precipitation"] = weather.precipitation.where(weather.precipitation.ge(0))
    weather["pressure_msl"] = weather.pressure_msl.where(weather.pressure_msl.gt(0))
    return air, weather, wind


def run(data_dir, wind_path, output, horizons, jobs, include_extension, downloads, include_fires=False):
    air, weather, wind = load_hourly_sources(data_dir, wind_path, include_extension, downloads)
    fires = load_detections(data_dir) if include_fires else None
    collected = {}
    with threadpool_limits(limits=jobs):
        for horizon in horizons:
            frame = build_matrix(air, weather, wind, horizon)
            if fires is not None:
                fire = fire_features(frame[KEY], fires, wind, horizon)
                frame = frame.merge(fire, on=KEY, how="left", validate="one_to_one")
            for name, result in compare_horizon(frame, horizon, jobs, include_fires).items():
                collected.setdefault(name, []).append(result.assign(horizonte_h=horizon))
    output.mkdir(parents=True, exist_ok=True)
    for name, tables in collected.items():
        pd.concat(tables, ignore_index=True).to_csv(output / f"{name}.csv", index=False, float_format="%.9g")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path)
    parser.add_argument("--salida", type=Path, default=Path("resultados/multihorizonte"))
    parser.add_argument("--horizontes", nargs="+", type=int, choices=range(1, 7), default=HORIZONS)
    parser.add_argument("--hilos", type=int, default=4)
    parser.add_argument("--extender", action="store_true", help="Evalúa también las lecturas de mayo-junio de OpenAQ")
    parser.add_argument("--focos", action="store_true", help="Compara predictores de focos térmicos VIIRS y su alineación")
    parser.add_argument("--descargas-clima", type=Path, help="Usa los CSV de clima de la extensión guardados localmente")
    args = parser.parse_args()
    if args.hilos < 1:
        parser.error("--hilos debe ser al menos 1")
    try:
        run(args.datos, args.viento or args.datos / WIND_FILE, args.salida,
            dict.fromkeys(args.horizontes), args.hilos, args.extender, args.descargas_clima, args.focos)
    except FileNotFoundError as error:
        parser.error(f"Falta un insumo: {error.filename or error}. Revisa las rutas de --datos y --viento.")


if __name__ == "__main__":
    main()
