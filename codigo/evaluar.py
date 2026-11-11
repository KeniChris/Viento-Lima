"""Validación cronológica, ablación y prueba histórica del pronóstico de PM2.5."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits
from xgboost import XGBRegressor

from .preparar import CURRENT, FEATURES, HOUR, KEY, STATION, TARGET, TARGET_TIME, TIME

SEED = 42
VALIDATION_YEARS = (2020, 2021, 2022)
TEST_START = pd.Timestamp("2023-01-01")
XGB_HITO2 = dict(n_estimators=220, max_depth=5, learning_rate=.05, subsample=.8,
                 colsample_bytree=.8, reg_lambda=5)
FIXED_ABLATION = ("xgb_hito2", "xgb_viento", "xgb_vecinos", "xgb_transporte")


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    group: str
    parameters: dict


CANDIDATES = (
    Candidate("xgb_hito2", "XGBoost", "base", XGB_HITO2),
    Candidate("xgb_viento", "XGBoost", "viento", XGB_HITO2),
    Candidate("xgb_vecinos", "XGBoost", "vecinos", XGB_HITO2),
    Candidate("xgb_transporte", "XGBoost", "transporte", XGB_HITO2),
    Candidate("xgb_transporte_d3", "XGBoost", "transporte",
              XGB_HITO2 | dict(n_estimators=180, max_depth=3, learning_rate=.07, reg_lambda=8, min_child_weight=15)),
    Candidate("xgb_transporte_d6", "XGBoost", "transporte",
              XGB_HITO2 | dict(n_estimators=180, max_depth=6, reg_lambda=12, min_child_weight=30)),
    Candidate("histgb_transporte_15", "HistGradientBoosting", "transporte",
              dict(max_iter=140, max_leaf_nodes=15, min_samples_leaf=60, learning_rate=.07, l2_regularization=3)),
    Candidate("histgb_transporte_31", "HistGradientBoosting", "transporte",
              dict(max_iter=120, max_leaf_nodes=31, min_samples_leaf=40, learning_rate=.07, l2_regularization=5)),
    Candidate("rf_transporte_d12", "RandomForest", "transporte",
              dict(n_estimators=48, max_depth=12, min_samples_leaf=20, max_features=.55, max_samples=.30)),
    Candidate("rf_transporte_d16", "RandomForest", "transporte",
              dict(n_estimators=48, max_depth=16, min_samples_leaf=15, max_features=.55, max_samples=.30)),
)


def make_pipeline(candidate, jobs):
    columns = FEATURES[candidate.group]
    numeric = [col for col in columns if col != STATION]
    preprocessing = ColumnTransformer([
        ("numericas", Pipeline([
            ("imputar", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
            ("escalar", StandardScaler()),
        ]), numeric),
        ("estacion", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [STATION]),
    ])
    if candidate.family == "XGBoost":
        model = XGBRegressor(tree_method="hist", n_jobs=jobs, random_state=SEED, **candidate.parameters)
    elif candidate.family == "HistGradientBoosting":
        model = HistGradientBoostingRegressor(early_stopping=False, random_state=SEED, **candidate.parameters)
    else:
        model = RandomForestRegressor(n_jobs=jobs, random_state=SEED, **candidate.parameters)
    return Pipeline([("preparar", preprocessing), ("modelo", model)])


def predictions(pipeline, rows):
    return np.maximum(pipeline.predict(rows), 0)


def references(rows):
    return {
        "persistencia": rows[CURRENT].to_numpy(),
        "estacional_24h": rows["pm25_estacional_24h"].fillna(rows[CURRENT]).to_numpy(),
    }


def score(rows, pred):
    actual = rows[TARGET].to_numpy()
    error = pred - actual
    absolute = pd.Series(np.abs(error), index=rows.index)
    return {
        "n": len(rows), "estaciones": rows[STATION].nunique(),
        "MAE": mean_absolute_error(actual, pred),
        "MAE_macro": absolute.groupby(rows[STATION]).mean().mean(),
        "RMSE": root_mean_squared_error(actual, pred),
        "R2": r2_score(actual, pred) if len(actual) >= 2 else np.nan,
        "sesgo": error.mean(),
    }


def windows(frame):
    for year in VALIDATION_YEARS:
        begin, end = pd.Timestamp(f"{year}-01-01"), pd.Timestamp(f"{year + 1}-01-01")
        train = frame.loc[frame[TARGET_TIME].lt(begin)]
        validation = frame.loc[frame[TIME].ge(begin) & frame[TARGET_TIME].lt(end)]
        if train.empty or validation.empty or train[TARGET_TIME].max() >= validation[TIME].min():
            raise ValueError(f"Ventana temporal {year} inválida")
        yield year, train, validation


def cross_validate(frame, jobs, candidates):
    metrics = []
    supports = []
    for year, train, val in windows(frame):
        print(f"Validación {year}, entrenamiento {len(train):,}, validación {len(val):,}", flush=True)
        counts = pd.concat([train[STATION].value_counts(), val[STATION].value_counts()], axis=1).fillna(0)
        counts.columns = ["n_entrenamiento", "n_validacion"]
        supports.append(counts.rename_axis(STATION).reset_index().assign(anio=year))
        for name, pred in references(val).items():
            metrics.append({"anio": year, "modelo": name, "familia": "Referencia", "grupo": "referencia", **score(val, pred)})
        for candidate in candidates:
            cols = FEATURES[candidate.group]
            pipeline = make_pipeline(candidate, jobs)
            pipeline.fit(train[cols], train[TARGET])
            performance = score(val, predictions(pipeline, val[cols]))
            metrics.append({"anio": year, "modelo": candidate.name, "familia": candidate.family,
                            "grupo": candidate.group, **performance})
            print(f"  {candidate.name:25s} MAE macro {performance['MAE_macro']:.4f}", flush=True)
    details = pd.DataFrame(metrics)
    summary = details.groupby(["modelo", "familia", "grupo"], as_index=False).agg(
        MAE_macro_cv=("MAE_macro", "mean"), DE_MAE_macro_cv=("MAE_macro", "std"),
        MAE_cv=("MAE", "mean"), RMSE_cv=("RMSE", "mean"), pliegues=("anio", "nunique"),
    ).sort_values(["MAE_macro_cv", "modelo"], ignore_index=True)
    return details, summary, pd.concat(supports, ignore_index=True)


def episode_metrics(test, pred, thresholds):
    expected = test[TARGET].to_numpy()
    cutoff = test[STATION].map(thresholds).to_numpy()
    high = expected >= cutoff
    predicted_high = pred >= cutoff
    hits = int((high & predicted_high).sum())
    return {
        "n_altos": int(high.sum()), "mae_altos": np.abs(pred[high] - expected[high]).mean(),
        "sesgo_altos": (pred[high] - expected[high]).mean(),
        "n_predichos_altos": int(predicted_high.sum()), "aciertos_altos": hits,
        "sensibilidad_altos": hits / high.sum() if high.any() else np.nan,
        "precision_altos": hits / predicted_high.sum() if predicted_high.any() else np.nan,
    }


def bootstrap_delta(test, a, b, n_resamples=2000):
    difference = np.abs(a - test[TARGET].to_numpy()) - np.abs(b - test[TARGET].to_numpy())
    blocks = ((test[TIME] - test[TIME].min().floor("D")).dt.days // 7).to_numpy()
    totals = pd.DataFrame({"block": blocks, "error": difference}).groupby("block").error.agg(["sum", "size"])
    choices = np.random.default_rng(SEED).integers(0, len(totals), size=(n_resamples, len(totals)))
    deltas = totals["sum"].to_numpy()[choices].sum(axis=1) / totals["size"].to_numpy()[choices].sum(axis=1)
    return {"delta_MAE": difference.mean(), "IC95_inferior": np.quantile(deltas, .025),
            "IC95_superior": np.quantile(deltas, .975), "bloques_7_dias": len(totals)}


def save_test_diagnostics(test, all_predictions, dev, winner, output):
    threshold = dev.groupby(STATION)[TARGET].quantile(.95)
    threshold.rename("p95_desarrollo").to_csv(output / "umbrales_altos.csv")
    pd.DataFrame([
        {"modelo": name, **score(test, pred), **episode_metrics(test, pred, threshold)}
        for name, pred in all_predictions.items()
    ]).sort_values("MAE_macro").to_csv(output / "prueba_modelos.csv", index=False)
    station_metrics = []
    for name in dict.fromkeys(("persistencia", "xgb_hito2", winner)):
        pred = pd.Series(all_predictions[name], index=test.index)
        for station, rows in test.groupby(STATION):
            station_pred = pred.loc[rows.index].to_numpy()
            station_metrics.append({"modelo": name, STATION: station, **score(rows, station_pred),
                                    **episode_metrics(rows, station_pred, threshold)})
    pd.DataFrame(station_metrics).to_csv(output / "prueba_por_estacion.csv", index=False)
    deltas = []
    for first, second in dict.fromkeys((
        ("xgb_viento", "xgb_hito2"), ("xgb_vecinos", "xgb_viento"), ("xgb_transporte", "xgb_vecinos"),
        (winner, "xgb_hito2"), (winner, "persistencia"),
    )):
        deltas.append({"modelo": first, "referencia": second,
                       **bootstrap_delta(test, all_predictions[first], all_predictions[second])})
    pd.DataFrame(deltas).to_csv(output / "diferencias_prueba.csv", index=False)
    all_aligned = test["transporte_origenes"].gt(0)
    stratified = []
    for label, mask in (("con_origen_alineado", all_aligned), ("sin_origen_alineado", ~all_aligned)):
        if mask.any():
            for name in ("xgb_vecinos", "xgb_transporte"):
                stratified.append({"condicion": label, "modelo": name,
                                   **score(test.loc[mask], all_predictions[name][mask.to_numpy()])})
    pd.DataFrame(stratified).to_csv(output / "prueba_por_alineacion.csv", index=False)
    prediction_table = test[KEY + [TARGET_TIME, TARGET]].assign(**all_predictions)
    prediction_table.to_parquet(output / "predicciones_prueba.parquet", index=False)


def evaluate_models(frame, output, jobs=4):
    if not frame[TARGET_TIME].sub(frame[TIME]).eq(HOUR).all() or frame.duplicated(KEY).any():
        raise ValueError("Matriz con claves duplicadas u horizonte distinto a t+1 h")
    output.mkdir(parents=True, exist_ok=True)
    candidates = {spec.name: spec for spec in CANDIDATES}
    with threadpool_limits(limits=jobs):
        details, cv, support = cross_validate(frame, jobs, CANDIDATES)
        details.to_csv(output / "cv_por_pliegue.csv", index=False)
        cv.to_csv(output / "cv_modelos.csv", index=False)
        support.to_csv(output / "cv_soporte_estaciones.csv", index=False)
        # Elegir por validación, antes de leer las etiquetas del periodo de prueba.
        advanced = cv.loc[cv.familia.ne("Referencia")]
        winner = advanced.iloc[0].modelo
        selected = advanced.drop_duplicates("familia").modelo.tolist()
        finalists = list(dict.fromkeys([*FIXED_ABLATION, *selected]))
        dev = frame.loc[frame[TARGET_TIME].lt(TEST_START)]
        test = frame.loc[frame[TIME].ge(TEST_START)]
        if dev[TARGET_TIME].max() >= test[TIME].min():
            raise ValueError("Solapamiento entre entrenamiento y prueba")
        pred = references(test)
        for name in finalists:
            candidate = candidates[name]
            cols = FEATURES[candidate.group]
            pipeline = make_pipeline(candidate, jobs)
            print(f"Reajuste anterior a 2023: {name}", flush=True)
            pipeline.fit(dev[cols], dev[TARGET])
            pred[name] = predictions(pipeline, test[cols])
            if name == winner:
                joblib.dump({
                    "pipeline": pipeline, "features": cols, "modelo": name, "horizonte_horas": 1,
                    "version_modelo": f"hito3-{name}-2022", "zona_horaria": "America/Lima",
                    "fin_objetivos_entrenamiento": str(dev[TARGET_TIME].max()),
                }, output / "modelo_seleccionado.joblib", compress=3)
        save_test_diagnostics(test, pred, dev, winner, output)

        # Dependencia predictiva exploratoria sobre validación 2022.
        _, train_2022, val_2022 = list(windows(frame))[-1]
        spec = candidates[winner]
        cols = FEATURES[spec.group]
        pipe = make_pipeline(spec, jobs).fit(train_2022[cols], train_2022[TARGET])
        sample = val_2022.groupby(STATION, group_keys=False).sample(n=250, random_state=SEED, replace=False)
        importance = permutation_importance(pipe, sample[cols], sample[TARGET],
                                            scoring="neg_mean_absolute_error", n_repeats=3, random_state=SEED)
        pd.DataFrame({"variable": cols, "incremento_MAE": importance.importances_mean,
                      "desviacion": importance.importances_std}).sort_values(
            "incremento_MAE", ascending=False
        ).to_csv(output / "permutacion_validacion_2022.csv", index=False)
    meta = {
        "modelo_seleccionado_cv": winner, "hiperparametros": candidates[winner].parameters,
        "criterio": "menor MAE macro por estación, media de tres pliegues",
        "inicio_emisiones_prueba": str(test[TIME].min()), "fin_objetivos_prueba": str(test[TARGET_TIME].max()),
        "n_desarrollo": len(dev), "n_prueba": len(test), "n_purgadas_corte_final": len(frame) - len(dev) - len(test),
        "mejor_referencia_cv": cv.loc[cv.familia.eq("Referencia"), "modelo"].iloc[0],
        "n_predictores": len(FEATURES[candidates[winner].group]),
        "candidatos": {name: {"grupo": c.group, "familia": c.family, **c.parameters} for name, c in candidates.items()},
    }
    (output / "resumen.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matriz", type=Path, default=Path("resultados/matriz_hito3.parquet"))
    parser.add_argument("--salida", type=Path, default=Path("resultados"))
    parser.add_argument("--hilos", type=int, default=4)
    args = parser.parse_args()
    if not args.matriz.is_file():
        parser.error(
            f"Falta {args.matriz}. Ejecuta primero python -m codigo.preparar "
            "o indica una matriz ya generada con --matriz."
        )
    data = pd.read_parquet(args.matriz)
    result = evaluate_models(data, args.salida, args.hilos)
    print(json.dumps(result, indent=2, ensure_ascii=False))
