"""Publica el pronóstico de PM2.5 a una hora para el dashboard."""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .actualizar_aire import PERU
from .cargar_redis import NoObservations, hour_key, load_observations, read_observations, redis_client
from .modelos_finales import HOURLY_VERSIONS, VERSION, features, load_bundle, load_sources, predict
from .pronosticar import CURRENT
from .viento import STATION, STATIONS, TIME, WIND_FILE


HOUR = pd.Timedelta(hours=1)


def lima_hour(value):
    issue = pd.Timestamp(value)
    if pd.isna(issue) or issue.tzinfo is None:
        raise ValueError("La emisión necesita un desfase explícito, por ejemplo -05:00")
    issue = issue.tz_convert(PERU)
    if issue != issue.floor("h"):
        raise ValueError("La emisión debe corresponder a una hora entera en Lima")
    return issue


def publish(client, bundle, stations, issue, *, historical=False, clock=None):
    issue = lima_hour(issue)
    target = issue + HOUR
    clock = clock or (lambda: pd.Timestamp.now(tz=PERU))
    version = bundle[1]["version"]
    local_issue = issue.tz_localize(None)
    try:
        air, weather, wind = read_observations(client, local_issue, version=version)
    except NoObservations:
        return [{"estacion": station, "estado": "SIN_DATOS_RECIENTES"} for station in stations]

    readings = air.loc[air[TIME].eq(local_issue) & air[STATION].isin(stations)]
    values = readings[CURRENT]
    ready = readings.loc[np.isfinite(values) & values.ge(0), STATION].tolist()
    if not ready:
        return [{"estacion": station, "estado": "SIN_DATOS_RECIENTES"} for station in stations]

    frame = features(air, weather, wind, 1, require_target=False)
    rows = frame.loc[frame[TIME].eq(local_issue) & frame[STATION].isin(ready)]
    estimated = predict(None, bundle, rows) if len(rows) else np.array([])
    if not np.isfinite(estimated).all():
        raise ValueError("El modelo produjo un pronóstico inválido")
    calculated = clock().tz_convert(PERU)
    available = calculated >= target if historical else issue <= calculated < target
    values_by_station = dict(zip(rows[STATION], estimated, strict=True))
    reports = []
    with client.pipeline(transaction=True) as pipe:
        for station in stations:
            if station not in ready:
                reports.append({"estacion": station, "estado": "SIN_DATOS_RECIENTES"})
                continue
            if not available or station not in values_by_station:
                reports.append({"estacion": station, "estado": "SIN_PRONOSTICO"})
                continue
            payload = {
                "estacion": station, "emitido_en": issue.isoformat(), "objetivo_en": target.isoformat(),
                "calculado_en": calculated.isoformat(), "horizonte_h": 1, "modelo": bundle[1]["modelo"],
                "version_modelo": version, "pm25_estimado": float(values_by_station[station]),
                "modo": "HISTORICO" if historical else "ACTUAL",
            }
            key = hour_key("pronostico", station, issue, 1, version=version)
            pipe.hset(key, mapping=payload)
            reports.append({**payload, "estado": "HISTORICO" if historical else "VIGENTE"})
        pipe.execute()
    return reports


def load_window(client, sources, issue, version):
    end = lima_hour(issue).tz_localize(None)
    start = end - 168 * HOUR
    window = [frame.loc[frame[TIME].between(start, end)] for frame in sources]
    return load_observations(client, *window, version=version, web_index=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=("cargar-historico", "publicar"))
    parser.add_argument("--modo", choices=("actual", "historico"), default="actual")
    parser.add_argument("--estacion", choices=STATIONS)
    parser.add_argument("--emision", help="Hora de emisión con desfase explícito")
    parser.add_argument("--version", choices=HOURLY_VERSIONS, default=os.environ.get("PM25_MODELO_VERSION", VERSION))
    parser.add_argument("--redis-url")
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--modelos", type=Path, default=Path("modelos"))
    parser.add_argument("--descargas-clima", type=Path)
    args = parser.parse_args()
    if args.version not in HOURLY_VERSIONS:
        parser.error("PM25_MODELO_VERSION debe corresponder a una versión de una hora")
    if (args.accion == "cargar-historico" or args.modo == "historico") and args.emision is None:
        parser.error("Indica --emision para el modo histórico")
    try:
        issue = lima_hour(args.emision) if args.emision else pd.Timestamp.now(tz=PERU).floor("h")
    except ValueError as error:
        parser.error(str(error))
    client = redis_client(args.redis_url)
    if args.accion == "cargar-historico":
        sources = load_sources(args.datos, args.datos / WIND_FILE, args.descargas_clima)
        result = {"version": args.version, "observaciones": load_window(client, sources, issue, args.version)}
    else:
        bundle = load_bundle(None, args.modelos, 1, version=args.version)
        stations = [args.estacion] if args.estacion else list(STATIONS)
        result = publish(client, bundle, stations, issue, historical=args.modo == "historico")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
