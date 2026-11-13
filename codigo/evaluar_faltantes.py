"""Mide el efecto de cortes de PM2.5 mediante una comparación pareada."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .analizar_mae import errors
from .evaluar import TEST_START, bootstrap_delta
from .evaluar_final import training_thresholds
from .evaluar_lstm import histories, make_sequences
from .modelos_finales import HOLDOUT_START, MODELS, configure_tensorflow, features, load_bundle, load_sources, predict
from .pronosticar import CURRENT, TARGET, TARGET_TIME
from .viento import STATION, TIME, WIND_FILE


CUTS = (1, 6, 12)


def outage(rows, sequence, hours):
    # Solo se pierden observaciones locales anteriores a la emisión.
    masked = sequence.copy()
    masked[:, -hours - 1:-1] = np.nan
    changed = rows.copy()
    for lag in (1, 2, 3, 6, 12):
        changed[f"pm_lag_{lag}h"] = masked[:, -lag - 1]

    last = pd.DataFrame(masked)
    six, three = last.iloc[:, -6:], last.iloc[:, -3:]
    has_six, has_day = six.count(axis=1).ge(4), last.count(axis=1).ge(12)
    changed["pm_media_6h"] = six.mean(axis=1).where(has_six).to_numpy()
    changed["pm_std_6h"] = six.std(axis=1).where(has_six).to_numpy()
    changed["pm_media_24h"] = last.mean(axis=1).where(has_day).to_numpy()
    changed["pm_desviacion_24h"] = last.std(axis=1).where(has_day).to_numpy()
    changed["pm_mediana_3h"] = three.median(axis=1).where(three.count(axis=1).ge(2)).to_numpy()
    changed["pm_cambio_1h"] = changed[CURRENT] - changed["pm_lag_1h"]
    changed["pm_cambio_3h"] = changed[CURRENT] - changed["pm_lag_3h"]
    return changed, masked


def run(tf, air, weather, wind, models, output):
    calendar, grid = make_sequences(air)
    records = []
    for horizon in MODELS:
        frame = features(air, weather, wind, horizon)
        thresholds = training_thresholds(frame.loc[frame[TARGET_TIME].lt(TEST_START)])
        rows = frame.loc[frame[TIME].ge(HOLDOUT_START)]
        sequence = histories(rows, calendar, grid)
        complete = np.isfinite(sequence).all(axis=1)
        rows, sequence = rows.loc[complete], sequence[complete]
        bundle = load_bundle(tf, models, horizon)
        original = predict(tf, bundle, rows, sequence)
        for hours in (0, *CUTS):
            estimate = original
            if hours:
                masked, missing = outage(rows, sequence, hours)
                estimate = predict(tf, bundle, masked, missing)
            records.append({"horizonte_h": horizon, "corte_h": hours,
                            **errors(rows, estimate, thresholds),
                            **bootstrap_delta(rows, estimate, original, target=TARGET)})
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(output / "faltantes.csv", index=False, float_format="%.9g")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path)
    parser.add_argument("--descargas-clima", type=Path)
    parser.add_argument("--modelos", type=Path, default=Path("modelos"))
    parser.add_argument("--salida", type=Path, default=Path("resultados/pronostico"))
    parser.add_argument("--hilos", type=int, default=3)
    args = parser.parse_args()
    tf = configure_tensorflow(args.hilos)
    sources = load_sources(args.datos, args.viento or args.datos / WIND_FILE, args.descargas_clima)
    with threadpool_limits(limits=args.hilos):
        run(tf, *sources, args.modelos, args.salida)


if __name__ == "__main__":
    main()
