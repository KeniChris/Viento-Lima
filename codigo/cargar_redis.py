"""Uso (desde la carpeta raiz de Viento-Lima, con redis-server.exe abierto):
    python cargar_redis.py            -> carga los datos en Redis
    python cargar_redis.py --dry-run  -> solo muestra los comandos, sin conectarse
"""

import argparse
import csv
from pathlib import Path

RES = Path("resultados")


def leer_csv(ruta):
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Simulado:

    def sadd(self, k, *v): print("SADD", k, *v)
    def hset(self, k, mapping): print("HSET", k, " ".join(f"{a} {b}" for a, b in mapping.items()))
    def set(self, k, v): print("SET", k, v[:60] + ("..." if len(v) > 60 else ""))
    def dbsize(self): return 0


def cargar(r):
    # 1) Estaciones (conjunto)
    umbrales = leer_csv(RES / "umbrales_altos.csv")
    r.sadd("estaciones", *[u["ESTACION"] for u in umbrales])

    # 2) Umbral P95 por estacion  -> umbral:CARABAYLLO
    for u in umbrales:
        r.hset(f"umbral:{u['ESTACION']}", mapping={"p95_desarrollo": u["p95_desarrollo"]})

    # 3) Pronosticos (equivale a la tabla pm25_hito3.pronosticos de PostgreSQL)
    #    -> pronostico:CARABAYLLO:2023-01-01
    for p in leer_csv(RES / "conexion_pronosticos.csv"):
        clave = f"pronostico:{p['ESTACION']}:{p['FECHA_HORA_LIMA']}"
        r.hset(clave, mapping={
            "estacion": p["ESTACION"],
            "emitido_en": p["FECHA_HORA_LIMA"],
            "objetivo_en": p["FECHA_OBJETIVO"],
            "version_modelo": p["version_modelo"],
            "pm25_estimado": p["pm25_estimado"],
        })

    # 4) Metricas de cada modelo en la prueba -> modelo:prueba:xgb_hito2
    for m in leer_csv(RES / "prueba_modelos.csv"):
        r.hset(f"modelo:prueba:{m['modelo']}", mapping=m)

    # 5) Modelo final por horizonte -> modelo:final:1h, 3h, 6h
    for m in leer_csv(RES / "cierre_modelos" / "modelos_finales.csv"):
        r.hset(f"modelo:final:{m['horizonte_h']}h", mapping=m)

    # 6) Resumen completo como texto JSON
    r.set("resumen:modelo", (RES / "resumen.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=6379)
    a = ap.parse_args()

    if a.dry_run:
        cliente = Simulado()
    else:
        import redis  # pip install redis
        cliente = redis.Redis(host=a.host, port=a.port, decode_responses=True, protocol=2)  # protocol=2: Redis 5 de Windows no soporta HELLO
        cliente.ping()

    cargar(cliente)
    if not a.dry_run:
        print("Listo. Claves en Redis:", cliente.dbsize())
