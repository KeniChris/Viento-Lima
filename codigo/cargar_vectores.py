"""Indexa ventanas históricas y recupera episodios de PM2.5 similares."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from redis.exceptions import ResponseError
from sklearn.preprocessing import StandardScaler

from .modelos_finales import HOLDOUT_START, VERSION, features, load_sources
from .pronosticar import CURRENT, TARGET, TARGET_TIME
from .viento import KEY, STATION, STATIONS, TIME, WIND_FILE


PREFIX = f"pm25:episodio:{VERSION}:"
INDEX = f"idx:pm25:episodios:{VERSION}"
SCALE_KEY = f"pm25:escala_episodios:{VERSION}"
VECTOR_FIELDS = [CURRENT, "pm_cambio_3h", "pm_media_6h", "relative_humidity_2m",
                 "viento_este_ms", "viento_norte_ms"]


def timestamp(time):
    t = pd.Timestamp(time)
    if t != t.floor("h"):
        raise ValueError("La fecha debe corresponder a una hora entera")
    if t.tzinfo is None:
        t = t.tz_localize("America/Lima")
    return int(t.timestamp())


def encode(vector):
    return np.asarray(vector, dtype="<f4").tobytes()


def index_windows(client, historical):
    rows = historical[KEY + VECTOR_FIELDS + [TARGET]].dropna().copy()
    rows = rows.loc[np.isfinite(rows[VECTOR_FIELDS + [TARGET]].to_numpy()).all(axis=1)]
    if rows.empty:
        raise ValueError("No hay ventanas de seis horas con vector y desenlace observados")
    scaler = StandardScaler().fit(rows[VECTOR_FIELDS])
    embeddings = scaler.transform(rows[VECTOR_FIELDS])
    try:
        client.execute_command("FT.INFO", INDEX)
    except ResponseError as exc:
        if "unknown index" not in str(exc).lower() and "no such index" not in str(exc).lower():
            raise
        try:
            client.execute_command(
                "FT.CREATE", INDEX, "ON", "HASH", "PREFIX", 1, PREFIX, "SCHEMA",
                "estacion", "TAG", "emitido_unix", "NUMERIC",
                "vector", "VECTOR", "FLAT", 6, "TYPE", "FLOAT32",
                "DIM", len(VECTOR_FIELDS), "DISTANCE_METRIC", "L2",
            )
        except ResponseError as creation:
            if "already exists" not in str(creation).lower():
                raise
    scale = json.dumps({"campos": VECTOR_FIELDS, "media": scaler.mean_.tolist(), "escala": scaler.scale_.tolist()})
    current_scale = client.get(SCALE_KEY)
    if current_scale is not None and current_scale != scale.encode():
        raise ValueError("El índice ya usa otra normalización. Asigna una versión nueva al conjunto de episodios.")
    client.set(SCALE_KEY, scale)

    pipeline = client.pipeline(transaction=False)
    values = rows[KEY + [CURRENT, TARGET]].itertuples(index=False, name=None)
    for i, ((station, when, level, target), vector) in enumerate(zip(values, embeddings, strict=True), 1):
        issued = timestamp(when)
        fields = {"estacion": station, "emitido_unix": issued, "vector": encode(vector),
                  "pm25_actual": level, "pm25_6h": target}
        pipeline.hset(f"{PREFIX}{station}:{issued}", mapping=fields)
        if i % 2000 == 0:
            pipeline.execute()
    pipeline.execute()
    return len(rows)


def similar(client, values, station, before, k=5):
    if station not in STATIONS or k < 1:
        raise ValueError("Indica una estación registrada y un número positivo de episodios")
    spec = json.loads(client.get(SCALE_KEY))
    data = np.asarray([values[name] for name in spec["campos"]], dtype=float)
    if not np.isfinite(data).all():
        raise ValueError("La consulta requiere los seis valores observados")
    latest = timestamp(before) - 6 * 3600
    query = f"(@estacion:{{{station}}} @emitido_unix:[-inf ({latest}])=>[KNN {k} @vector $v AS distancia]"
    result = client.execute_command(
        "FT.SEARCH", INDEX, query, "PARAMS", 2, "v",
        encode((data - spec["media"]) / spec["escala"]),
        "SORTBY", "distancia", "RETURN", 5, "estacion", "emitido_unix", "pm25_actual", "pm25_6h", "distancia",
        "LIMIT", 0, k, "DIALECT", 2,
    )
    matches = []
    for key, raw in zip(result[1::2], result[2::2]):
        item = {a.decode(): b.decode() for a, b in zip(raw[::2], raw[1::2])}
        item.update(clave=key.decode(), distancia=float(item["distancia"]),
                    pm25_actual=float(item["pm25_actual"]), pm25_6h=float(item["pm25_6h"]))
        item["emitido_en"] = pd.Timestamp(int(item.pop("emitido_unix")), unit="s", tz="UTC").tz_convert("America/Lima").isoformat()
        matches.append(item)
    return matches


def history_before(air, weather, wind):
    rows = features(air, weather, wind, 6)
    return rows.loc[rows[TARGET_TIME].lt(HOLDOUT_START)]


def main():
    from .cargar_redis import read_input, redis_client, write_inputs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--descargas-clima", type=Path)
    parser.add_argument("--redis-url")
    parser.add_argument("--estacion", choices=STATIONS)
    parser.add_argument("--emision")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    client = redis_client(args.redis_url)
    if args.emision:
        if not args.estacion:
            parser.error("--emision requiere --estacion")
        when = pd.Timestamp(timestamp(args.emision), unit="s", tz="UTC").tz_convert("America/Lima").tz_localize(None)
        write_inputs(client, args.estacion, when)
        row, _ = read_input(client, args.estacion, when, 6)
        print(json.dumps(similar(client, row.iloc[0], args.estacion, args.emision, args.k), ensure_ascii=False, indent=2))
    else:
        sources = load_sources(args.datos, args.datos / WIND_FILE, args.descargas_clima)
        print(f"Episodios indexados: {index_windows(client, history_before(*sources))}")


if __name__ == "__main__":
    main()
