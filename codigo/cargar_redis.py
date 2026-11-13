"""Carga observaciones y métricas en Redis y ejecuta pronósticos por estación."""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import redis
from redis.exceptions import ResponseError

from .analizar_mae import GROUP_BY_HOUR, HISTORY
from .cargar_vectores import VECTOR_FIELDS, history_before, index_windows, similar, timestamp
from .evaluar_lstm import histories, make_sequences
from .modelos_finales import HOLDOUT_START, MODELS, VERSION, configure_tensorflow, features, load_bundle, load_sources, predict
from .preparar import METEO
from .pronosticar import CURRENT, GROUPS, TARGET_TIME
from .viento import DIRECTION, KEY, SPEED, STATION, STATIONS, TIME, WIND_FILE


RESULTS = Path("resultados")
DEFAULT_URL = "redis://127.0.0.1:6379/0"


def redis_client(url=None):
    client = redis.Redis.from_url(url or os.environ.get("PM25_REDIS_URL", DEFAULT_URL),
                                 decode_responses=False, protocol=2, socket_timeout=30)
    client.ping()
    return client


def json_value(value):
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    return value


def pack(mapping):
    return json.dumps(mapping, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def hour_key(kind, station, when, horizon=None):
    suffix = f":{horizon}h" if horizon is not None else ""
    return f"pm25:{kind}:{VERSION}:{station}:{timestamp(when)}{suffix}"


def load_observations(client, air, weather, wind):
    records = air[KEY + [CURRENT]].merge(weather[KEY + METEO[:4]], on=KEY, how="outer", validate="one_to_one")
    records = records.merge(wind[KEY + [SPEED, DIRECTION]], on=KEY, how="outer", validate="one_to_one")
    records = records.dropna(subset=[CURRENT, *METEO[:4], SPEED, DIRECTION], how="all")
    fields = [CURRENT, *METEO[:4], SPEED, DIRECTION]
    pipeline = client.pipeline(transaction=False)
    for i, (station, time, *values) in enumerate(records[KEY + fields].itertuples(index=False, name=None), 1):
        source = "SENAMHI" if time < pd.Timestamp("2024-05-01") else "SENAMHI vía OpenAQ"
        payload = dict(zip(fields, map(json_value, values), strict=True))
        payload.update(estacion=station, fecha_unix=timestamp(time),
                       fuente_pm25=source if payload[CURRENT] is not None else None,
                       fuente_meteorologia="ERA5", fuente_viento="IFS a 10 m")
        pipeline.set(hour_key("observacion", station, time), pack(payload))
        if i % 2000 == 0:
            pipeline.execute()
    pipeline.execute()
    return len(records)


def load_metrics(client, results):
    # Las claves de resumen también son accesibles para el módulo de preguntas.
    evaluation = pd.read_csv(results / "pronostico" / "prueba.csv")
    choices = json.loads((results / "pronostico" / "modelos.json").read_text(encoding="utf-8"))
    by_station = pd.read_csv(results / "pronostico" / "por_estacion.csv")
    confusion = pd.read_csv(results / "alertas" / "matriz_confusion.csv")
    thresholds = pd.read_csv(results / "alertas" / "umbrales_2021.csv")
    with client.pipeline(transaction=True) as pipe:
        pipe.sadd("estaciones", *STATIONS)
        for station, (lat, lon) in STATIONS.items():
            pipe.hset(f"pm25:estacion:{station}", mapping={"latitud": lat, "longitud": lon})
        for row in thresholds.to_dict("records"):
            pipe.hset(f"pm25:umbral:{VERSION}:{row[STATION]}", mapping={
                "p75_pm25": row["p75_pm25"], "p95_aumento_3h": row["p95_aumento_3h"],
            })
        for item in choices:
            horizon, name = item["horizonte_h"], item["modelo"]
            row = evaluation.loc[evaluation.horizonte_h.eq(horizon) & evaluation.modelo.eq(name)].iloc[0]
            pipe.hset(f"modelo:final:{horizon}h", mapping=item | {
                "modelo_final": name, "periodo_prueba": "octubre–diciembre 2024",
                "MAE": float(row.MAE), "MAE_altos": float(row.MAE_altos), "n": int(row.n),
            })
        for row in by_station.to_dict("records"):
            key = f"pm25:metrica:{VERSION}:{row[STATION]}:{row['horizonte_h']}h:{row['modelo']}"
            pipe.set(key, pack({name: json_value(value) for name, value in row.items()}))
        for row in confusion.to_dict("records"):
            key = f"pm25:alerta:{VERSION}:{row['periodo']}:{row['metodo']}"
            pipe.set(key, pack({name: json_value(value) for name, value in row.items()}))
        pipe.set("resumen:modelo", pack({"version_modelo": VERSION, "modelos": choices}))
        pipe.execute()


def read_observations(client, when):
    calendar = pd.date_range(when - pd.Timedelta(hours=168), when, freq="h")
    keys = [hour_key("observacion", station, t) for station in STATIONS for t in calendar]
    records = [json.loads(value) for value in client.mget(keys) if value is not None]
    if not records:
        raise ValueError("Redis no contiene observaciones para la ventana solicitada")
    rows = pd.DataFrame(records).rename(columns={"estacion": STATION})
    rows[TIME] = pd.to_datetime(rows.pop("fecha_unix"), unit="s", utc=True).dt.tz_convert("America/Lima").dt.tz_localize(None)
    columns = [CURRENT, *METEO[:4], SPEED, DIRECTION]
    rows[columns] = rows[columns].astype(float)
    return rows[KEY + [CURRENT]], rows[KEY + METEO[:4]], rows[KEY + [SPEED, DIRECTION]]


def write_inputs(client, station, when):
    air, weather, wind = read_observations(client, when)
    calendar, grid = make_sequences(air)
    records = []
    for horizon in MODELS:
        frame = features(air, weather, wind, horizon, require_target=False)
        rows = frame.loc[frame[STATION].eq(station) & frame[TIME].eq(when)]
        if len(rows) != 1:
            raise ValueError("La estación no tiene una lectura de PM2.5 en la hora de emisión")
        columns = list(dict.fromkeys(GROUPS[GROUP_BY_HOUR[horizon]] + HISTORY + VECTOR_FIELDS))
        values = {name: json_value(value) for name, value in rows[columns].iloc[0].to_dict().items()}
        sequence = [json_value(float(v)) for v in histories(rows, calendar, grid)[0]]
        records.append((hour_key("entrada", station, when, horizon), pack({"variables": values, "historia": sequence})))
    with client.pipeline(transaction=True) as pipe:
        for key, data in records:
            pipe.set(key, data)
        pipe.execute()


def read_input(client, station, when, horizon):
    raw = client.get(hour_key("entrada", station, when, horizon))
    if raw is None:
        raise KeyError(f"Falta la entrada de {station} a {horizon} h en Redis")
    values = json.loads(raw)
    rows = pd.DataFrame([values["variables"]])
    columns = rows.columns.drop(STATION)
    rows[columns] = rows[columns].astype(float)
    return rows, np.asarray([values["historia"]], dtype=np.float32)


def forecast_redis(client, tf, models, station, when):
    issued = pd.Timestamp(timestamp(when), unit="s", tz="UTC").tz_convert("America/Lima")
    forecasts = []
    for horizon in MODELS:
        rows, history = read_input(client, station, issued, horizon)
        bundle = load_bundle(tf, models, horizon)
        estimate = float(predict(tf, bundle, rows, history)[0])
        if not np.isfinite(estimate):
            raise ValueError(f"El modelo de {horizon} h produjo un pronóstico inválido")
        forecasts.append({"estacion": station, "emitido_en": issued.isoformat(),
                          "objetivo_en": (issued + pd.Timedelta(hours=horizon)).isoformat(),
                          "horizonte_h": horizon, "modelo": bundle[1]["modelo"],
                          "version_modelo": VERSION, "pm25_estimado": estimate})
    with client.pipeline(transaction=True) as pipe:
        for item in forecasts:
            pipe.hset(hour_key("pronostico", station, issued, item["horizonte_h"]), mapping=item)
        pipe.execute()
    return forecasts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=["cargar", "pronosticar", "similares", "verificar"])
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--descargas-clima", type=Path)
    parser.add_argument("--modelos", type=Path, default=Path("modelos"))
    parser.add_argument("--resultados", type=Path, default=RESULTS)
    parser.add_argument("--redis-url")
    parser.add_argument("--estacion", choices=STATIONS, default="SAN_BORJA")
    parser.add_argument("--emision", default="2024-11-15T08:00:00-05:00")
    args = parser.parse_args()
    client = redis_client(args.redis_url)
    when = pd.Timestamp(timestamp(args.emision), unit="s", tz="UTC").tz_convert("America/Lima").tz_localize(None)
    if when != when.floor("h"):
        parser.error("--emision debe corresponder a una hora entera")
    if args.accion == "cargar":
        try:
            client.execute_command("FT._LIST")
        except ResponseError as exc:
            parser.error(f"El servidor necesita Redis Search con soporte vectorial. {exc}")
        sources = load_sources(args.datos, args.datos / WIND_FILE, args.descargas_clima)
        observations = load_observations(client, *sources)
        episodes = index_windows(client, history_before(*sources))
        load_metrics(client, args.resultados)
        print(pack({"observaciones": observations, "episodios": episodes}))
        return
    write_inputs(client, args.estacion, when)
    if args.accion == "similares":
        rows, _ = read_input(client, args.estacion, when, 6)
        print(json.dumps(similar(client, rows.iloc[0], args.estacion, when), ensure_ascii=False, indent=2))
        return
    tf = configure_tensorflow(3)
    generated = forecast_redis(client, tf, args.modelos, args.estacion, when)
    if args.accion == "pronosticar":
        print(json.dumps(generated, ensure_ascii=False, indent=2))
        return

    from .verificar_redis import verify

    evidence = verify(client, tf, args.modelos, args.resultados, args.estacion, when, generated)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
