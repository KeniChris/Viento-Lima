"""Prueba temporal adicional con lecturas SENAMHI distribuidas por OpenAQ."""

import argparse
from io import StringIO
from pathlib import Path
from urllib.request import urlopen

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .actualizar_aire import EXTENSION_END, EXTENSION_FILE, EXTENSION_START, PERU, UTC_TIME
from .evaluar import CANDIDATES, FIXED_ABLATION, TEST_START, bootstrap_delta, make_pipeline, predictions, references, score
from .preparar import CURRENT, FEATURES, METEO, TARGET, TARGET_TIME
from .preparar import add_hito2_sources, load_air, local_features, spatial_features
from .viento import KEY, STATION, STATIONS, TIME, clean_wind, parse_wind_csv, request_url, validate_hours

WEATHER_START = "2024-04-29"
WEATHER_END = "2024-12-31"


def weather_response(model, hourly, downloads):
    if downloads is not None:
        file_name = "era5_2024.csv" if model == "era5" else "ifs_2024.csv"
        return (downloads / file_name).read_text(encoding="utf-8-sig")
    url = request_url(WEATHER_START, WEATHER_END, model=model, hourly=",".join(hourly))
    with urlopen(url, timeout=90) as response:
        return response.read().decode("utf-8-sig")


def extension_meteorology(downloads):
    era_response = weather_response("era5", METEO, downloads)
    metadata, hourly_csv = era_response.replace("\r\n", "\n").split("\n\n", maxsplit=1)
    metadata = pd.read_csv(StringIO(metadata))
    if metadata.location_id.tolist() != list(range(len(STATIONS))) or not metadata.timezone.eq(PERU).all():
        raise ValueError("ERA5 no contiene las estaciones solicitadas en hora de Lima")
    meteo = pd.read_csv(StringIO(hourly_csv)).rename(columns=lambda col: col.split(" (")[0])
    meteo[STATION] = meteo.pop("location_id").map(dict(enumerate(STATIONS)))
    meteo[TIME] = pd.to_datetime(meteo.pop("time"), format="%Y-%m-%dT%H:%M")
    meteo = meteo[KEY + METEO]
    validate_hours(meteo, "ERA5 de extensión")
    ifs_response = weather_response("ecmwf_ifs", METEO[-2:], downloads)
    wind = parse_wind_csv(ifs_response, WEATHER_START, WEATHER_END)
    clean_wind(wind)
    return meteo, wind


def extended_air(data_dir):
    new = pd.read_csv(data_dir / EXTENSION_FILE, parse_dates=[TIME, UTC_TIME])
    validate_hours(new, "PM2.5 de OpenAQ")
    reconstructed = new[UTC_TIME].dt.tz_convert(PERU).dt.tz_localize(None)
    if not new[TIME].eq(reconstructed).all() or not new[TIME].between(EXTENSION_START, EXTENSION_END).all():
        raise ValueError("La extensión debe tener horas de Lima consistentes y posteriores al CSV original")
    raw, _ = load_air(data_dir)
    seed = raw.loc[raw[TIME].ge(pd.Timestamp(WEATHER_START)), KEY + [CURRENT]]
    combined = pd.concat([seed, new[KEY + [CURRENT]]], ignore_index=True)
    validate_hours(combined, "aire combinado")
    keys = pd.MultiIndex.from_product([
        STATIONS, pd.date_range(WEATHER_START, EXTENSION_END, freq="h")
    ], names=KEY)
    return combined.set_index(KEY).reindex(keys).reset_index()


def extension_matrix(data_dir, downloads):
    air = extended_air(data_dir)
    meteo, wind = extension_meteorology(downloads)
    # Para el rezago de las primeras emisiones se conservan las horas históricas solapadas.
    historical = pd.read_parquet(data_dir / "clima_era5_7_estaciones.parquet", columns=KEY + METEO)
    historical = historical.loc[historical[TIME].ge(pd.Timestamp(WEATHER_START))]
    overlap = historical.merge(meteo, on=KEY, suffixes=("_hito2", "_descarga"), validate="one_to_one")
    comparisons = pd.DataFrame([{
        "variable": name, "horas_solapadas": len(overlap),
        "horas_con_mismo_valor": int(np.isclose(overlap[f"{name}_hito2"], overlap[f"{name}_descarga"]).sum()),
    } for name in METEO])
    meteo = pd.concat([historical, meteo.loc[meteo[TIME].ge(EXTENSION_START)]], ignore_index=True)
    base = add_hito2_sources(local_features(air, meteo), data_dir)
    spatial, _ = spatial_features(air, wind)
    matrix = base.merge(spatial, on=KEY, validate="one_to_one")
    matrix = matrix.loc[matrix[TIME].between(EXTENSION_START, EXTENSION_END)]
    matrix = matrix.sort_values([TIME, STATION], ignore_index=True)
    if matrix.empty:
        raise ValueError("La extensión no contiene horas con predictores y objetivo")
    return matrix, comparisons


def evaluate_extension(data_dir, output_dir, downloads=None, jobs=4):
    matrix, weather_check = extension_matrix(data_dir, downloads)
    main = pd.read_parquet(output_dir / "matriz_hito3.parquet")
    dev = main.loc[main[TARGET_TIME].lt(TEST_START)]
    bundle = joblib.load(output_dir / "modelo_seleccionado.joblib")
    pred = references(matrix)
    with threadpool_limits(limits=jobs):
        pred[bundle["modelo"]] = predictions(bundle["pipeline"], matrix[bundle["features"]])
        for c in (candidate for candidate in CANDIDATES if candidate.name in FIXED_ABLATION):
            cols = FEATURES[c.group]
            model = make_pipeline(c, jobs).fit(dev[cols], dev[TARGET])
            pred[c.name] = predictions(model, matrix[cols])
    results = pd.DataFrame([{"modelo": name, **score(matrix, values)} for name, values in pred.items()])
    results = results.sort_values("MAE", ignore_index=True)
    results.to_csv(output_dir / "prueba_extension_2024.csv", index=False)
    weather_check.to_csv(output_dir / "comparacion_era5_extension.csv", index=False)
    pd.DataFrame([bootstrap_delta(matrix, pred["xgb_transporte"], pred["xgb_vecinos"])]).to_csv(
        output_dir / "diferencia_transporte_extension_2024.csv", index=False
    )
    matrix.groupby(STATION)[TARGET_TIME].agg(["count", "min", "max"]).rename(columns={
        "count": "n", "min": "primer_objetivo_lima", "max": "ultimo_objetivo_lima"
    }).to_csv(output_dir / "soporte_extension_2024.csv")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--resultados", type=Path, default=Path("resultados/historico"))
    parser.add_argument("--descargas-clima", type=Path)
    parser.add_argument("--hilos", type=int, default=4)
    args = parser.parse_args()
    result = evaluate_extension(args.datos, args.resultados, args.descargas_clima, args.hilos)
    print(result.to_string(index=False))
