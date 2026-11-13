"""Contrasta el pronóstico almacenado y la búsqueda vectorial con sus referencias."""

import json

import numpy as np
import pandas as pd

from .cargar_vectores import PREFIX, SCALE_KEY, VECTOR_FIELDS, similar, timestamp
from .modelos_finales import MODELS, VERSION
from .viento import STATION, TIME


def verify(client, tf, models, results, station, when, generated):
    from .cargar_redis import forecast_redis, hour_key, read_input, write_inputs

    expected = pd.read_parquet(results / "pronostico" / "predicciones.parquet")
    expected = expected.loc[expected[STATION].eq(station) & expected[TIME].eq(when)].set_index("horizonte_h")
    if set(expected.index) != set(MODELS):
        raise ValueError("Selecciona una emisión con los tres objetivos observados en la prueba")
    written = pd.DataFrame(generated).set_index("horizonte_h")
    difference = (expected.pm25_estimado - written.pm25_estimado).abs()
    np.testing.assert_allclose(written.pm25_estimado, expected.pm25_estimado, rtol=0, atol=1e-4)
    write_inputs(client, station, when)
    again = forecast_redis(client, tf, models, station, when)
    if again != generated:
        raise AssertionError("Repetir la misma emisión cambió el pronóstico")
    for horizon in MODELS:
        key = hour_key("pronostico", station, when, horizon)
        saved = float(client.hget(key, "pm25_estimado"))
        np.testing.assert_allclose(saved, written.loc[horizon, "pm25_estimado"], rtol=0, atol=1e-9)

    rows, _ = read_input(client, station, when, 6)
    matches = similar(client, rows.iloc[0], station, when)
    scale = json.loads(client.get(SCALE_KEY))
    vector = (rows[VECTOR_FIELDS].iloc[0].to_numpy(dtype=float) - scale["media"]) / scale["escala"]
    keys = list(client.scan_iter(f"{PREFIX}{station}:*", count=2000))
    candidates = [k for k in keys if int(k.rsplit(b":", 1)[-1]) < timestamp(when) - 6 * 3600]
    with client.pipeline(transaction=False) as pipe:
        for key in candidates:
            pipe.hget(key, "vector")
        raw = pipe.execute()
    matrix = np.stack([np.frombuffer(blob, dtype="<f4") for blob in raw])
    direct = np.square(matrix - vector.astype(np.float32)).sum(axis=1)
    rank = np.sort(direct)[:5]
    np.testing.assert_allclose([r["distancia"] for r in matches], rank, rtol=1e-5, atol=1e-6)

    evidence = {
        "redis": client.info("server")["redis_version"],
        "estacion": station,
        "emision_lima": generated[0]["emitido_en"],
        "pronosticos": generated,
        "observaciones_cargadas": sum(1 for _ in client.scan_iter(f"pm25:observacion:{VERSION}:*", count=5000)),
        "episodios_indexados": sum(1 for _ in client.scan_iter(PREFIX + "*", count=5000)),
        "vecinos_recuperados": len(matches),
        "primer_vecino": matches[0],
        "consulta_coincide_con_busqueda_exacta": True,
        "repeticion_sin_duplicados": True,
        "diferencia_maxima_ug_m3": float(difference.max()),
    }
    (results / "pronostico" / "redis.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                                                       encoding="utf-8")
    return evidence
