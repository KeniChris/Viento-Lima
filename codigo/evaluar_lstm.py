"""Evalúa LSTM y los modelos de referencia en las pruebas temporales de PM2.5."""

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

from .actualizar_aire import EXTENSION_START
from .analizar_mae import BASELINE, GROUP_BY_HOUR, HISTORY, errors, historical_features
from .evaluar import SEED, TEST_START, score, windows
from .preparar_lecturas_recientes import START as RECENT_START, load_clean
from .pronosticar import CURRENT, GROUPS, HORIZONS, TARGET, TARGET_TIME, build_matrix, candidates
from .pronosticar import fit_and_predict, load_hourly_sources, station_matrix
from .viento import KEY, STATION, STATIONS, TIME, WIND_FILE, validate_hours


LOOKBACK = 24
UNITS = 16
EPOCHS = 8
BATCH_SIZE = 512
MODEL = "lstm_24h"
RECENT = "openaq_jul_sep_2024"
ROBUST_XGB = "xgb_historia_mae"


def make_sequences(air):
    calendar = pd.date_range(air[TIME].min(), air[TIME].max(), freq="h")
    grid = station_matrix(air, CURRENT, calendar).to_numpy(dtype=np.float32)
    grid = np.pad(grid, ((LOOKBACK - 1, 0), (0, 0)), constant_values=np.nan)
    return calendar, np.lib.stride_tricks.sliding_window_view(grid, LOOKBACK, axis=0)


def histories(rows, calendar, grid):
    hours = calendar.get_indexer(rows[TIME])
    stations = pd.Index(STATIONS).get_indexer(rows[STATION])
    if (hours < 0).any() or (stations < 0).any():
        raise ValueError("Una emisión no pertenece al calendario horario de las estaciones")
    sequence = grid[hours, stations]
    np.testing.assert_allclose(sequence[:, -1], rows[CURRENT], rtol=1e-6)
    return sequence


def normalize_history(sequence, center, scale):
    available = np.isfinite(sequence)
    standardized = np.where(available, (sequence - center) / scale, 0)
    return np.stack([standardized, available], axis=-1).astype(np.float32)


def dataset(tf, inputs, target=None):
    values = (inputs,) if target is None else (inputs, target)
    batches = tf.data.Dataset.from_tensor_slices(values)
    if target is not None:
        batches = batches.shuffle(min(len(target), 16384), seed=SEED)
    options = tf.data.Options()
    options.threading.private_threadpool_size = 2
    return batches.batch(BATCH_SIZE).with_options(options).prefetch(1)


def fit_and_forecast(tf, train, evaluations, calendar, grid, horizon):
    tf.keras.backend.clear_session()
    tf.keras.utils.set_random_seed(SEED)
    columns = GROUPS[GROUP_BY_HOUR[horizon]] + HISTORY
    numeric = [column for column in columns if column != STATION]
    preprocessing = ColumnTransformer([
        ("numericas", make_pipeline(
            SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            StandardScaler(),
        ), numeric),
        ("estacion", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [STATION]),
    ]).fit(train[columns])
    sequence = histories(train, calendar, grid)
    center = float(np.nanmedian(sequence))
    scale = max(1.0, float(np.nanstd(sequence)))

    def inputs(rows):
        sequence = normalize_history(histories(rows, calendar, grid), center, scale)
        tabular = preprocessing.transform(rows[columns]).astype(np.float32)
        return sequence, tabular

    train_inputs = inputs(train)
    history = tf.keras.Input(shape=(LOOKBACK, 2), name="historia_pm25")
    tabular = tf.keras.Input(shape=(train_inputs[1].shape[1],), name="predictores")
    state = tf.keras.layers.LSTM(UNITS)(history)
    dense = tf.keras.layers.Dense(24, activation="relu")(tf.keras.layers.Concatenate()([state, tabular]))
    change = tf.keras.layers.Dense(1, kernel_initializer="zeros")(dense)
    model = tf.keras.Model([history, tabular], change)
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=0.001), loss="mae")

    actual_change = (train[TARGET] - train[CURRENT]).to_numpy(dtype=np.float32)
    target_scale = max(1.0, float(actual_change.std()))
    model.fit(dataset(tf, train_inputs, actual_change[:, None] / target_scale), epochs=EPOCHS, shuffle=False, verbose=0)
    predictions = []
    for rows in evaluations:
        estimated = model.predict(dataset(tf, inputs(rows)), verbose=0).ravel()
        predictions.append(np.maximum(0, rows[CURRENT].to_numpy() + target_scale * estimated))
    return predictions


def compare(tf, frame, calendar, grid, horizon):
    development = frame.loc[frame[TARGET_TIME].lt(TEST_START)]
    folds = []
    for year, train, validation in windows(development):
        pred, = fit_and_forecast(tf, train, [validation], calendar, grid, horizon)
        folds.append({"anio": year, "modelo": MODEL, **score(validation, pred, TARGET)})
        print(f"LSTM +{horizon} h · validación {year} · MAE macro {folds[-1]['MAE_macro']:.4f}", flush=True)

    periods = {
        "historica": frame.loc[frame[TIME].ge(TEST_START) & frame[TARGET_TIME].lt(EXTENSION_START)],
        "openaq_2024": frame.loc[frame[TIME].ge(EXTENSION_START) & frame[TARGET_TIME].lt(RECENT_START)],
        RECENT: frame.loc[frame[TIME].ge(RECENT_START)],
    }
    periods = {name: rows for name, rows in periods.items() if not rows.empty}
    predictions = fit_and_forecast(tf, development, list(periods.values()), calendar, grid, horizon)
    thresholds = development.groupby(STATION)[TARGET].quantile(.95)
    metrics, by_station = [], []
    for (name, rows), pred in zip(periods.items(), predictions, strict=True):
        metrics.append({"periodo": name, "modelo": MODEL, **errors(rows, pred, thresholds)})
        for station, group in rows.groupby(STATION):
            station_pred = pred[rows.index.get_indexer(group.index)]
            by_station.append({"periodo": name, STATION: station, "modelo": MODEL,
                               **errors(group, station_pred, thresholds)})
        print(f"LSTM +{horizon} h · {name} · MAE {metrics[-1]['MAE']:.4f}", flush=True)
    return {"pliegues": folds, "prueba": metrics, "por_estacion": by_station}


def recent_tree_results(frame, horizon, jobs):
    development = frame.loc[frame[TARGET_TIME].lt(TEST_START)]
    recent = frame.loc[frame[TIME].ge(RECENT_START)]
    if recent.empty:
        raise ValueError("El periodo adicional no contiene pares emisión-objetivo evaluables")
    specs = {spec.name: spec for spec in candidates()}
    group = GROUP_BY_HOUR[horizon]
    xgb = specs[f"xgb_{group}"]
    reference, = fit_and_predict(xgb, development, [recent], jobs)
    if horizon == 1:
        histgb, = fit_and_predict(specs["histgb_vecinos_15"], development, [recent], jobs)
        reference = (reference + histgb) / 2
    robust_xgb = replace(xgb, parameters=xgb.parameters | {"objective": "reg:absoluteerror"})
    candidate, = fit_and_predict(robust_xgb, development, [recent], jobs, GROUPS[group] + HISTORY)
    thresholds = development.groupby(STATION)[TARGET].quantile(.95)
    result = []
    for name, prediction in (("persistencia", recent[CURRENT].to_numpy()), (BASELINE, reference),
                             (ROBUST_XGB, candidate)):
        result.append({"periodo": RECENT, "modelo": name, **errors(recent, prediction, thresholds)})
    return result


def summarize(tables, diagnostic, output):
    tree_cv = pd.read_csv(diagnostic / "validacion.csv")
    tree_cv = tree_cv.loc[tree_cv.modelo.isin([BASELINE, ROBUST_XGB])]
    lstm_cv = pd.DataFrame(tables["pliegues"]).groupby(["horizonte_h", "modelo"], as_index=False).agg(
        MAE_macro_cv=("MAE_macro", "mean"), DE_MAE_macro_cv=("MAE_macro", "std"),
    )
    validation = pd.concat([tree_cv[lstm_cv.columns], lstm_cv], ignore_index=True)
    validation = validation.sort_values(["horizonte_h", "MAE_macro_cv", "modelo"], ignore_index=True)
    lstm_test = pd.DataFrame(tables["prueba"])
    test = pd.concat([
        pd.DataFrame(tables["prueba_adicional_arboles"]), lstm_test.loc[lstm_test.periodo.eq(RECENT)],
    ], ignore_index=True).sort_values(["horizonte_h", "MAE_macro", "modelo"], ignore_index=True)

    finals = []
    for horizon, rows in validation.groupby("horizonte_h", sort=True):
        candidate = rows.iloc[0].modelo
        recent = test.loc[test.horizonte_h.eq(horizon)].set_index("modelo")
        if recent[["n", "estaciones", "n_altos"]].nunique().ne(1).any():
            raise ValueError(f"Los modelos de {horizon} horas deben usar las mismas filas")
        reference = recent.loc[BASELINE]
        best = recent.loc[candidate]
        selected = candidate if best.MAE_macro < reference.MAE_macro else BASELINE
        chosen = recent.loc[selected]
        finals.append({
            "horizonte_h": horizon, "candidato_validacion": candidate, "modelo_final": selected,
            "MAE_macro_cv_final": rows.set_index("modelo").loc[selected, "MAE_macro_cv"],
            "MAE_macro_cv_referencia": rows.set_index("modelo").loc[BASELINE, "MAE_macro_cv"],
            "pares_jul_sep_2024": int(chosen.n), "MAE_jul_sep": chosen.MAE, "MAE_referencia_jul_sep": reference.MAE,
            "MAE_macro_jul_sep": chosen.MAE_macro, "MAE_macro_referencia_jul_sep": reference.MAE_macro,
            "MAE_altos_jul_sep": chosen.MAE_altos, "MAE_altos_referencia_jul_sep": reference.MAE_altos,
        })

    for name, data in (("validacion_final", validation), ("prueba_jul_sep_2024", test),
                       ("modelos_finales", pd.DataFrame(finals))):
        data.to_csv(output / f"{name}.csv", index=False, float_format="%.9g")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path)
    parser.add_argument("--descargas-clima", type=Path)
    parser.add_argument("--extender", action="store_true")
    parser.add_argument("--aire-reciente", type=Path, help="CSV horario limpio de julio a septiembre")
    parser.add_argument("--salida", type=Path, default=Path("resultados/cierre_modelos"))
    parser.add_argument("--diagnostico", type=Path, default=Path("resultados/analisis_mae"))
    parser.add_argument("--horizontes", type=int, choices=HORIZONS, nargs="+")
    parser.add_argument("--hilos", type=int, default=3)
    args = parser.parse_args()
    if args.hilos < 1:
        parser.error("--hilos debe ser al menos 1")
    horizons = tuple(dict.fromkeys(args.horizontes or (HORIZONS if args.aire_reciente else (3, 6))))
    if 1 in horizons and not args.aire_reciente:
        parser.error("El horizonte de una hora evalúa los árboles del periodo adicional")
    if set(horizons) & {3, 6}:
        try:
            import tensorflow as tf
        except ModuleNotFoundError as exc:
            parser.error(f"Instala TensorFlow para este experimento: {exc}")
        tf.config.threading.set_intra_op_parallelism_threads(args.hilos)
        tf.config.threading.set_inter_op_parallelism_threads(1)
        tf.config.experimental.enable_op_determinism()

    air, weather, wind = load_hourly_sources(
        args.datos, args.viento or args.datos / WIND_FILE, args.extender or args.aire_reciente is not None,
        args.descargas_clima,
    )
    if args.aire_reciente:
        air = pd.concat([air[KEY + [CURRENT]], load_clean(args.aire_reciente)], ignore_index=True)
        validate_hours(air, "SENAMHI y OpenAQ")
    calendar, grid = make_sequences(air)
    output = {}
    with threadpool_limits(limits=args.hilos):
        for horizon in horizons:
            rows = build_matrix(air, weather, wind, horizon).merge(
                historical_features(air, horizon), on=KEY, validate="one_to_one",
            )
            tables = compare(tf, rows, calendar, grid, horizon) if horizon != 1 else {}
            if args.aire_reciente:
                tables["prueba_adicional_arboles"] = recent_tree_results(rows, horizon, args.hilos)
            for table, records in tables.items():
                output.setdefault(table, []).extend(dict(row, horizonte_h=horizon) for row in records)
    args.salida.mkdir(parents=True, exist_ok=True)
    complete = args.aire_reciente is not None and set(horizons) == set(HORIZONS)
    lstm_tables = {"pliegues", "prueba", "por_estacion"}
    for name, records in output.items():
        if complete and name == "prueba_adicional_arboles":
            continue
        filename = f"{name}_lstm.csv" if name in lstm_tables else f"{name}.csv"
        pd.DataFrame(records).to_csv(args.salida / filename, index=False, float_format="%.9g")
    if complete:
        summarize(output, args.diagnostico, args.salida)


if __name__ == "__main__":
    main()
