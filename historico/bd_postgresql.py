"""Reproduce la integración inicial de una hora en PostgreSQL."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import joblib
import numpy as np
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from codigo.evaluar import TEST_START, predictions
from codigo.preparar import STATION, TIME

ZONE = "America/Lima"
DDL = Path(__file__).with_name("contrato_postgresql.sql")


@contextmanager
def temporary_postgres():
    import pgserver

    binaries = Path(pgserver.__file__).parent / "pginstall" / "bin"
    with TemporaryDirectory(prefix="pm25-postgresql-") as directory:
        root = Path(directory)
        data = root / "data"
        password = secrets.token_urlsafe(24)
        pw_file = root / "pw"
        pw_file.write_text(password + "\n", encoding="ascii")
        pw_file.chmod(0o600)
        subprocess.run(
            [str(binaries / "initdb"), "-D", str(data), "--username=pm25",
             "--auth=scram-sha-256", "--pwfile", str(pw_file), "--no-locale", "--encoding=UTF8"],
            check=True, capture_output=True, text=True,
        )
        pw_file.unlink()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        with (root / "postgres.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [str(binaries / "postgres"), "-D", str(data), "-h", "127.0.0.1", "-p", str(port),
                 "-c", "unix_socket_directories="],
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 12
                while server.poll() is None:
                    try:
                        conn = psycopg.connect(
                            host="127.0.0.1", port=port, dbname="postgres", user="pm25", password=password,
                            autocommit=True, connect_timeout=2,
                        )
                    except psycopg.OperationalError:
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(.15)
                    else:
                        with conn:
                            yield conn
                        return
                details = (root / "postgres.log").read_text(encoding="utf-8").splitlines()[-5:]
                raise RuntimeError("No se pudo iniciar PostgreSQL local. " + " ".join(details))
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()


def seed_demo_inputs(conn, rows, columns):
    numeric = [name for name in columns if name != STATION]
    records = []
    for row in rows[[STATION, TIME, *numeric]].to_dict("records"):
        station = row.pop(STATION)
        issued = row.pop(TIME).tz_localize(ZONE).to_pydatetime()
        payload = {name: (float(value) if pd.notna(value) else None) for name, value in row.items()}
        records.append((station, issued, Jsonb(payload, dumps=lambda obj: json.dumps(obj, allow_nan=False)),
                        "Reproducción histórica de SENAMHI y meteorología"))
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO pm25_hito3.entradas (estacion, emitido_en, predictores, fuente)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (estacion, emitido_en) DO UPDATE
            SET predictores = EXCLUDED.predictores, fuente = EXCLUDED.fuente
        """, records)


def forecast_hour(conn, issued, bundle):
    if issued.tzinfo is None:
        raise ValueError("La hora de emisión debe incluir la zona horaria")
    columns = bundle["features"]
    required = set(columns) - {STATION}
    with conn.transaction(), conn.cursor() as cur:
        rows = cur.execute("""
            SELECT estacion, emitido_en, predictores
            FROM pm25_hito3.entradas WHERE emitido_en = %s
            ORDER BY estacion FOR SHARE
        """, (issued,)).fetchall()
        if not rows:
            raise ValueError(f"No se encontraron entradas para {issued.isoformat()}")
        features = []
        for station, _, values in rows:
            absent = required - values.keys()
            if absent:
                raise ValueError(f"{station}: faltan predictores en el contrato: {sorted(absent)}")
            features.append({**values, STATION: station})
        predicted = predictions(bundle["pipeline"], pd.DataFrame(features)[columns])
        if not np.isfinite(predicted).all():
            raise ValueError("El modelo produjo valores no finitos")
        cur.executemany("""
            INSERT INTO pm25_hito3.pronosticos (estacion, emitido_en, objetivo_en, version_modelo, pm25_estimado)
            VALUES (%s, %s, %s + INTERVAL '1 hour', %s, %s)
            ON CONFLICT (estacion, emitido_en, version_modelo) DO UPDATE
            SET pm25_estimado = EXCLUDED.pm25_estimado, registrado_en = now()
        """, [(station, emission, emission, bundle["version_modelo"], float(pm))
              for (station, emission, _), pm in zip(rows, predicted)])
    return len(rows)


def local_demo(bundle, output):
    matrix = pd.read_parquet(output / "matriz_hito3.parquet")
    test = matrix.loc[matrix[TIME].ge(TEST_START)]
    example = test.groupby(TIME).size().idxmax()
    inputs = test.loc[test[TIME].eq(example)]
    issued = example.tz_localize(ZONE).to_pydatetime()
    expected = pd.read_parquet(output / "predicciones_prueba.parquet")
    expected = expected.loc[expected[TIME].eq(example)].set_index(STATION)[bundle["modelo"]]
    with temporary_postgres() as conn:
        conn.execute(DDL.read_text(encoding="utf-8"))
        seed_demo_inputs(conn, inputs, bundle["features"])
        written = forecast_hour(conn, issued, bundle)
        forecast_hour(conn, issued, bundle)
        pg_version = conn.execute("SHOW server_version").fetchone()[0]
        readback = conn.execute("""
            SELECT estacion, emitido_en AT TIME ZONE 'America/Lima',
                   objetivo_en AT TIME ZONE 'America/Lima', version_modelo, pm25_estimado
            FROM pm25_hito3.pronosticos ORDER BY estacion
        """).fetchall()
    evidence = pd.DataFrame(readback, columns=[STATION, TIME, "FECHA_OBJETIVO", "version_modelo", "pm25_estimado"])
    difference = evidence.pm25_estimado.to_numpy() - expected.loc[evidence[STATION]].to_numpy()
    if len(evidence) != written or not np.allclose(difference, 0, atol=1e-5, rtol=0):
        raise RuntimeError("El readback de PostgreSQL difiere de la predicción histórica")
    evidence.to_csv(output / "conexion_pronosticos.csv", index=False)
    report = {
        "entorno": "PostgreSQL local temporal", "version_postgresql": pg_version,
        "fuente": "estaciones-hora históricas de la prueba", "emision_lima": example.isoformat(),
        "filas_leidas_y_escritas": written, "filas_despues_de_repetir": len(evidence),
        "error_maximo_frente_al_notebook": float(np.abs(difference).max()),
        "tabla_entrada": "pm25_hito3.entradas", "tabla_salida": "pm25_hito3.pronosticos",
    }
    (output / "conexion_postgresql.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo-local", action="store_true")
    group.add_argument("--emitido-en", help="Fecha y hora ISO 8601, con offset, en una base ya provisionada")
    parser.add_argument("--resultados", type=Path, default=Path("resultados/historico"))
    args = parser.parse_args()
    model = joblib.load(args.resultados / "modelo_seleccionado.joblib")
    if args.demo_local:
        print(json.dumps(local_demo(model, args.resultados), indent=2))
    else:
        timestamp = pd.Timestamp(args.emitido_en).to_pydatetime()
        with psycopg.connect(os.environ["PM25_DATABASE_URL"], autocommit=True) as connection:
            print(f"Pronósticos escritos: {forecast_hour(connection, timestamp, model)}")
