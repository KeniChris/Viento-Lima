"""Convierte los resultados por estacion en vectores y los guarda en Redis (busqueda vectorial)."""

"""Cada estacion (7 en total) queda representada por un vector de 8 numeros, calculado con el
modelo seleccionado (rf_transporte_d16) sobre la prueba historica:
    MAE, RMSE, R2, sesgo, MAE en lecturas altas, sensibilidad, precision, umbral P95.
Los numeros se normalizan a la escala 0-1 para que ninguna metrica pese mas que otra.
"""

import argparse
import csv
import math
import struct
from pathlib import Path

RES = Path("resultados")
MODELO = "rf_transporte_d16"
INDICE = "idx:estaciones"
PREFIJO = "vec:estacion:"
CAMPOS = ["MAE", "RMSE", "R2", "sesgo", "mae_altos", "sensibilidad_altos", "precision_altos"]
NOMBRES = CAMPOS + ["p95_desarrollo"]
DIM = len(NOMBRES)


def leer_csv(ruta):
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def construir_vectores():
    p95 = {r["ESTACION"]: float(r["p95_desarrollo"]) for r in leer_csv(RES / "umbrales_altos.csv")}
    crudo = {}
    for r in leer_csv(RES / "prueba_por_estacion.csv"):
        if r["modelo"] == MODELO:
            crudo[r["ESTACION"]] = [float(r[c]) for c in CAMPOS] + [p95[r["ESTACION"]]]
    # Normalizacion min-max por columna (0 a 1)
    normal = {e: [] for e in crudo}
    for j in range(DIM):
        col = [v[j] for v in crudo.values()]
        lo, hi = min(col), max(col)
        for e, v in crudo.items():
            normal[e].append((v[j] - lo) / (hi - lo) if hi > lo else 0.0)
    return crudo, normal


def a_bytes(vector):
    return struct.pack(f"<{len(vector)}f", *vector)


def distancia_l2_al_cuadrado(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def vecinos_locales(normal, estacion, k):
    """Calculo directo en Python, para comparar con lo que devuelve Redis."""
    otros = [(distancia_l2_al_cuadrado(normal[estacion], v), e) for e, v in normal.items() if e != estacion]
    return sorted(otros)[:k]


def texto(x):
    return x.decode("utf-8") if isinstance(x, bytes) else str(x)


def buscar(r, estacion, k):
    consulta = f"*=>[KNN {k + 1} @embedding $vec AS distancia]"
    resp = r.execute_command(
        "FT.SEARCH", INDICE, consulta,
        "PARAMS", "2", "vec", a_bytes(NORMAL[estacion]),
        "SORTBY", "distancia", "RETURN", "2", "estacion", "distancia", "DIALECT", "2",
    )
    resultados = []
    for i in range(1, len(resp), 2):
        campos = resp[i + 1]
        d = {texto(campos[j]): texto(campos[j + 1]) for j in range(0, len(campos), 2)}
        if d["estacion"] != estacion:
            resultados.append((float(d["distancia"]), d["estacion"]))
    return resultados[:k]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--estacion", default="CARABAYLLO")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=6379)
    a = ap.parse_args()

    CRUDO, NORMAL = construir_vectores()
    if a.estacion not in NORMAL:
        raise SystemExit(f"Estacion desconocida. Opciones: {', '.join(NORMAL)}")

    print(f"Vectores de {DIM} dimensiones ({', '.join(NOMBRES)}), modelo {MODELO}")
    for e, v in NORMAL.items():
        print(f"  {e:26s}", " ".join(f"{x:.2f}" for x in v))

    esperado = vecinos_locales(NORMAL, a.estacion, a.k)
    print(f"\nCalculo directo en Python: estaciones mas parecidas a {a.estacion}")
    for d, e in esperado:
        print(f"  {e:26s} distancia^2 = {d:.4f}")

    if a.dry_run:
        print("\n(--dry-run: no se conecto a Redis)")
        raise SystemExit

    import redis  # pip install redis
    r = redis.Redis(host=a.host, port=a.port, decode_responses=False, protocol=2)
    r.ping()


    try:
        r.execute_command("FT.DROPINDEX", INDICE, "DD")
    except redis.exceptions.ResponseError:
        pass

    r.execute_command(
        "FT.CREATE", INDICE, "ON", "HASH", "PREFIX", "1", PREFIJO,
        "SCHEMA", "estacion", "TAG",
        "embedding", "VECTOR", "FLAT", "6", "TYPE", "FLOAT32", "DIM", str(DIM), "DISTANCE_METRIC", "L2",
    )
    for e, v in NORMAL.items():
        r.hset(PREFIJO + e, mapping={"estacion": e, "embedding": a_bytes(v)})
    print(f"\nIndice {INDICE} creado con {len(NORMAL)} vectores en Redis.")

    print(f"\nRedis (KNN): estaciones mas parecidas a {a.estacion}")
    for d, e in buscar(r, a.estacion, a.k):
        print(f"  {e:26s} distancia = {d:.4f}")

