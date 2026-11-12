"""Evalúa alertas de PM2.5 entre estaciones después de una subida anómala."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .actualizar_aire import EXTENSION_START
from .evaluar import SEED, TEST_START
from .preparar import CURRENT, HOUR, load_air, station_geometry, wind_components
from .pronosticar import START, station_matrix
from .validar_extension import WEATHER_END, WEATHER_START, extended_air, weather_response
from .viento import CALM_MS, DIRECTION, KEY, SPEED, STATIONS, TIME, WIND_FILE
from .viento import clean_wind, load_wind, parse_wind_csv, validate_hours


HORIZON = 6
RADIUS_KM = 20
ANGLE_LIMIT = np.cos(np.deg2rad(45))
ORIGIN = "origen"
RECEIVER = "receptora"
TARGET = "alza_receptora"
BASE = ["distancia_km", "velocidad_ms", "pm_origen", "alza_origen_3h", "pm_receptora", "cambio_receptora_3h",
        "hora_seno", "hora_coseno", "mes_seno", "mes_coseno", "fin_de_semana"]
DIRECTIONAL = ["alineacion_cos", "avance_relativo_6h", "compatible"]


def station_signals(air):
    calendar = pd.date_range(START - 3 * HOUR, air[TIME].max(), freq="h")
    pm = station_matrix(air, CURRENT, calendar)
    change = pm.diff(3)
    development = (calendar >= START) & (calendar < TEST_START)
    threshold_level = pm.loc[development].quantile(.75)
    threshold_change = change.loc[development].quantile(.95)
    if threshold_level.isna().any() or threshold_change.isna().any():
        raise ValueError("Faltan observaciones de desarrollo para estimar umbrales por estación")
    thresholds = pd.DataFrame({"p75_pm25": threshold_level, "p95_aumento_3h": threshold_change})
    valid = pm.notna() & change.notna()
    anomaly = pm.ge(threshold_level) & change.ge(threshold_change) & change.gt(0)
    recent_anomalies = anomaly.shift(1, fill_value=False).rolling(HORIZON).sum()
    recent_valid = valid.shift(1, fill_value=False).rolling(HORIZON).sum()
    onset = anomaly & recent_anomalies.eq(0) & recent_valid.eq(HORIZON)
    normal = pm.lt(threshold_level) & valid
    observed, future = {}, {}
    for station in STATIONS:
        valid_window = np.column_stack([valid[station].shift(-h, fill_value=False) for h in range(1, HORIZON + 1)])
        events = np.column_stack([anomaly[station].shift(-h, fill_value=False) for h in range(1, HORIZON + 1)])
        observed[station] = valid_window.all(axis=1)
        future[station] = np.where(events.any(axis=1), events.argmax(axis=1) + 1, np.nan)
    return pm, change, thresholds, onset, normal, observed, future


def wind_route(speed, direction, distance, east, north):
    u, v = wind_components(speed, direction)
    parallel = u * east + v * north
    alignment = parallel / np.maximum(speed, CALM_MS)
    reach = speed.ge(CALM_MS) & (speed * 3.6 * HORIZON).ge(distance)
    relative_travel = parallel * 3.6 * HORIZON / distance
    aligned = reach & alignment.ge(ANGLE_LIMIT) & relative_travel.ge(1)
    eta = distance / (3.6 * parallel.where(aligned))
    return reach, aligned, alignment, relative_travel, eta


def make_cases(air, wind):
    pm, change, thresholds, onset, normal, observed, future = station_signals(air)
    speed = station_matrix(wind, SPEED, pm.index)
    direction = station_matrix(wind, DIRECTION, pm.index)
    frames = []
    for source in STATIONS:
        triggered = onset[source] & speed[source].notna() & direction[source].notna()
        for destination in STATIONS:
            if source == destination:
                continue
            distance, east, north = station_geometry(source, destination)
            if distance > RADIUS_KM:
                continue
            eligible = triggered & normal[destination]
            emission = pm.index[eligible]
            current_speed = speed.loc[emission, source]
            reachable, compatible, alignment, relative_travel, eta = wind_route(
                current_speed, direction.loc[emission, source], distance, east, north
            )
            hour_angle = 2 * np.pi * emission.hour / 24
            month_angle = 2 * np.pi * emission.month / 12
            first_h = future[destination][eligible.to_numpy()]
            frames.append(pd.DataFrame({
                TIME: emission, ORIGIN: source, RECEIVER: destination, "par": f"{source}/{destination}",
                "distancia_km": distance, "velocidad_ms": current_speed.to_numpy(),
                "pm_origen": pm.loc[emission, source].to_numpy(), "alza_origen_3h": change.loc[emission, source].to_numpy(),
                "pm_receptora": pm.loc[emission, destination].to_numpy(),
                "cambio_receptora_3h": change.loc[emission, destination].to_numpy(),
                "hora_seno": np.sin(hour_angle), "hora_coseno": np.cos(hour_angle),
                "mes_seno": np.sin(month_angle), "mes_coseno": np.cos(month_angle),
                "fin_de_semana": emission.dayofweek >= 5, "alcanzable_por_velocidad": reachable.to_numpy(),
                "compatible": compatible.to_numpy(), "alineacion_cos": alignment.to_numpy(),
                "avance_relativo_6h": relative_travel.to_numpy(), "llegada_estimada_h": eta.to_numpy(),
                "ventana_observada": observed[destination][eligible.to_numpy()],
                "primera_alza_h": first_h, TARGET: np.isfinite(first_h),
            }))
    return pd.concat(frames, ignore_index=True).sort_values([TIME, ORIGIN, RECEIVER], ignore_index=True), thresholds


def score_model(train, test, numerical):
    predictors = [*numerical, "par"]
    transform = ColumnTransformer([
        ("numericas", StandardScaler(), numerical),
        ("par", OneHotEncoder(handle_unknown="ignore"), ["par"]),
    ])
    model = make_pipeline(transform, LogisticRegression(C=1, max_iter=500))
    model.fit(train[predictors], train[TARGET])
    return model.predict_proba(test[predictors])[:, 1]


def alert_metrics(labels, alerts):
    hits = int((labels & alerts).sum())
    n_alerts, positives = int(alerts.sum()), int(labels.sum())
    return {"alertas": n_alerts, "aciertos": hits, "falsas_alertas": n_alerts - hits,
            "precision": hits / n_alerts if n_alerts else np.nan,
            "sensibilidad": hits / positives if positives else np.nan}


def weekly_delta(rows, a, b):
    week = rows[TIME].dt.to_period("W-SUN")
    outcome = rows[TARGET].to_numpy()
    counts = pd.DataFrame({"semana": week, "aciertos_a": outcome & a, "n_a": a,
                           "aciertos_b": outcome & b, "n_b": b}).groupby("semana").sum().to_numpy()
    result = {"bloques_semanales": len(counts), "IC95_inf": np.nan, "IC95_sup": np.nan}
    if len(counts) < 2 or not (a.any() and b.any()):
        return result
    sampled = np.random.default_rng(SEED).integers(0, len(counts), size=(2000, len(counts)))
    sums = counts[sampled].sum(axis=1)
    valid = (sums[:, 1] > 0) & (sums[:, 3] > 0)
    sums = sums[valid]
    delta = sums[:, 0] / sums[:, 1] - sums[:, 2] / sums[:, 3]
    if len(delta):
        result["IC95_inf"], result["IC95_sup"] = np.quantile(delta, [.025, .975])
    return result


def evaluate(train, candidates):
    rows = candidates.loc[candidates.ventana_observada].reset_index(drop=True)
    y = rows[TARGET].to_numpy()
    aligned = rows.compatible.to_numpy()
    reached = rows.alcanzable_por_velocidad.to_numpy()
    other = reached & ~aligned
    coverage = {"pares_candidatos": len(candidates), "pares_evaluables": len(rows), "alzas_receptoras": int(y.sum()),
                "episodios_origen": rows[[TIME, ORIGIN]].drop_duplicates().shape[0],
                "estaciones_origen": rows[ORIGIN].nunique(), "estaciones_receptoras": rows[RECEIVER].nunique()}
    rates = []
    for label, mask in (("compatible", aligned), ("otro_alcanzable_por_velocidad", other),
                        ("no_alcanzable_por_velocidad", ~reached)):
        total, hits = int(mask.sum()), int((mask & y).sum())
        rates.append({"grupo": label, "pares": total, "alzas": hits, "tasa_alza": hits / total if total else np.nan})

    # Igual número de avisos para aislar la capacidad de priorizar vecinas, sin usar etiquetas para decidir cuántos.
    budget = int(aligned.sum())
    rules = [{"metodo": "regla_viento", "precision_promedio": np.nan, **alert_metrics(y, aligned)}]
    alerts = []
    for label, columns in (("modelo_sin_rumbo", BASE), ("modelo_con_rumbo", BASE + DIRECTIONAL)):
        probability = score_model(train, rows, columns)
        warn = np.zeros(len(rows), dtype=bool)
        warn[np.argsort(-probability, kind="stable")[:budget]] = True
        alerts.append(warn)
        rules.append({"metodo": label, "precision_promedio": average_precision_score(y, probability) if y.any() else np.nan,
                      **alert_metrics(y, warn)})
    deltas = [
        {"comparacion": "tasa_compatible_menos_otro_alcanzable", "diferencia": rates[0]["tasa_alza"] - rates[1]["tasa_alza"],
         **weekly_delta(rows, aligned, other)},
        {"comparacion": "precision_modelo_con_rumbo_menos_sin_rumbo", "diferencia": rules[2]["precision"] - rules[1]["precision"],
         **weekly_delta(rows, alerts[1], alerts[0])},
    ]
    return {"cobertura": [coverage], "direccion": rates, "alertas": rules, "diferencias": deltas}


def run(data_dir, wind_path, output, extension=False, downloads=None):
    air, _ = load_air(data_dir)
    wind, _ = load_wind(wind_path)
    if extension:
        new = extended_air(data_dir)
        ifs = weather_response("ecmwf_ifs", ("wind_speed_10m", "wind_direction_10m"), downloads)
        newer_wind = parse_wind_csv(ifs, WEATHER_START, WEATHER_END)
        clean_wind(newer_wind)
        air = pd.concat([air[KEY + [CURRENT]], new.loc[new[TIME].ge(EXTENSION_START)]], ignore_index=True)
        wind = pd.concat([wind, newer_wind.loc[newer_wind[TIME].ge(EXTENSION_START)]], ignore_index=True)
    validate_hours(air, "PM2.5 por estación")
    validate_hours(wind, "viento IFS por estación")
    cases, thresholds = make_cases(air, wind)
    window_end = cases[TIME] + HORIZON * HOUR
    train = cases.loc[cases.ventana_observada & window_end.lt(TEST_START)]
    if train[TARGET].nunique() != 2:
        raise ValueError("Se requieren alzas y no alzas observadas antes de 2023")
    periods = {"historica": cases.loc[cases[TIME].ge(TEST_START) & window_end.lt(EXTENSION_START)]}
    if extension:
        periods["openaq_2024"] = cases.loc[cases[TIME].ge(EXTENSION_START)]
    results = {}
    for name, cohort in periods.items():
        if not cohort.ventana_observada.any():
            raise ValueError(f"El periodo {name} carece de ventanas completas para evaluar")
        for table, entries in evaluate(train, cohort).items():
            results.setdefault(table, []).extend({"periodo": name, **entry} for entry in entries)
    output.mkdir(parents=True, exist_ok=True)
    thresholds.to_csv(output / "umbrales.csv", index_label="ESTACION", float_format="%.9g")
    for name, entries in results.items():
        pd.DataFrame(entries).to_csv(output / f"{name}.csv", index=False, float_format="%.9g")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path)
    parser.add_argument("--salida", type=Path, default=Path("resultados/eventos_viento"))
    parser.add_argument("--extender", action="store_true", help="Evalúa también la extensión de OpenAQ de mayo–junio de 2024")
    parser.add_argument("--descargas-clima", type=Path, help="Carpeta con ifs_2024.csv para la extensión")
    args = parser.parse_args()
    try:
        results = run(args.datos, args.viento or args.datos / WIND_FILE, args.salida, args.extender, args.descargas_clima)
    except FileNotFoundError as error:
        parser.error(f"Falta un insumo: {error.filename or error}. Revisa --datos y --viento.")
    print(pd.DataFrame(results["alertas"]).to_string(index=False))


if __name__ == "__main__":
    main()
