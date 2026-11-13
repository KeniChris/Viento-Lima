"""Descarga, limpia y describe la prueba posterior de PM2.5 de OpenAQ."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import numpy as np
import pandas as pd

from .actualizar_aire import ARCHIVE, EXTENSION_END, LOCATIONS, PERU, UNIT, UTC_TIME, VALUE, daily_key
from .viento import KEY, STATION, TIME, validate_hours


START = EXTENSION_END + pd.Timedelta(hours=1)
END = pd.Timestamp("2024-09-30 23:00")
FILENAME = "pm25_senamhi_openaq_jul_sep_2024.csv.gz"
TEST_START = END + pd.Timedelta(hours=1)
TEST_END = pd.Timestamp("2024-12-31 23:00")
TEST_FILE = "pm25_senamhi_openaq_oct_dic_2024.csv.gz"


def fetch_day(item, cache):
    station, date = item
    location = LOCATIONS[station]
    key = daily_key(location, date)
    if cache is not None:
        path = cache / Path(key).name
        status, content = (200, path.read_bytes()) if path.is_file() else ("sin_archivo", None)
    else:
        content, status = None, "timeout"
        for _ in range(2):
            try:
                with urlopen(f"{ARCHIVE}/{key}", timeout=18) as response:
                    content, status = response.read(), response.status
                break
            except HTTPError as exc:
                status = exc.code
                if status == 404:
                    break
            except (URLError, TimeoutError):
                status = "timeout"
    if content is None:
        return station, date, status, None
    frame = pd.read_csv(BytesIO(content), compression="gzip")
    return station, date, status, frame.loc[frame.parameter.eq("pm25")].assign(**{STATION: station})


def clean_readings(raw, start=START, end=END):
    utc = pd.to_datetime(raw.datetime, utc=True, format="ISO8601", errors="coerce")
    local = utc.dt.tz_convert(PERU).dt.tz_localize(None)
    value = pd.to_numeric(raw.value, errors="coerce")
    within = local.between(start, end)
    invalid = pd.DataFrame({
        "fecha_invalida": utc.isna(),
        "hora_no_entera": within & utc.ne(utc.dt.floor("h")),
        "estacion_invalida": within & raw.location_id.ne(raw[STATION].map(LOCATIONS)),
        "unidad_invalida": within & raw.units.ne(UNIT),
        "valor_invalido": within & ~(np.isfinite(value) & value.ge(0)),
    }, index=raw.index)
    valid = within & ~invalid.any(axis=1)
    cleaned = raw.loc[valid, [STATION]].assign(**{TIME: local, UTC_TIME: utc, VALUE: value})
    exact = cleaned.duplicated(KEY + [VALUE])
    removed_duplicates = cleaned.loc[exact].groupby(STATION).size()
    cleaned = cleaned.loc[~exact]
    conflict = cleaned.duplicated(KEY, keep=False)
    removed_conflicts = cleaned.loc[conflict].groupby(STATION).size()
    cleaned = cleaned.loc[~conflict].sort_values(KEY, ignore_index=True)
    validate_hours(cleaned, "PM2.5 de OpenAQ")

    audit = invalid.assign(**{
        STATION: raw[STATION], "fuera_periodo": utc.notna() & ~within, "filas_pm25": 1,
        "filas_invalidas": invalid.any(axis=1),
    }).groupby(STATION).sum().reindex(LOCATIONS, fill_value=0)
    audit["duplicados_exactos"] = removed_duplicates.reindex(audit.index, fill_value=0)
    audit["filas_en_conflicto"] = removed_conflicts.reindex(audit.index, fill_value=0)
    distribution = cleaned.groupby(STATION)[VALUE].agg(
        horas_con_medicion="size", minimo="min", mediana="median", p95=lambda x: x.quantile(.95), maximo="max",
    )
    audit = audit.join(distribution)
    audit["horas_con_medicion"] = audit.horas_con_medicion.fillna(0).astype(int)
    audit["horas_esperadas"] = len(pd.date_range(start, end, freq="h"))
    audit["horas_sin_medicion"] = audit.horas_esperadas - audit.horas_con_medicion
    audit["cobertura"] = audit.horas_con_medicion / audit.horas_esperadas
    audit["unidad"] = UNIT
    return cleaned, audit.reset_index()


def load_clean(path, start=START, end=END):
    frame = pd.read_csv(path, parse_dates=[TIME, UTC_TIME])
    validate_hours(frame, "PM2.5 reciente")
    reconstructed = frame[UTC_TIME].dt.tz_convert(PERU).dt.tz_localize(None)
    if (not frame[TIME].eq(reconstructed).all() or not frame[TIME].between(start, end).all()
            or not frame[STATION].isin(LOCATIONS).all()
            or not (np.isfinite(frame[VALUE]) & frame[VALUE].ge(0)).all()):
        raise ValueError("El PM2.5 reciente debe pasar por la limpieza antes de evaluarse")
    return frame[KEY + [VALUE]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--salida", type=Path)
    parser.add_argument("--archivos", type=Path, help="Directorio con archivos diarios originales de OpenAQ")
    parser.add_argument("--periodo", choices=["jul-sep", "oct-dic"], default="jul-sep")
    args = parser.parse_args()
    start, end, filename, suffix = (
        (TEST_START, TEST_END, TEST_FILE, "oct_dic")
        if args.periodo == "oct-dic" else (START, END, FILENAME, "jul_sep")
    )
    output = args.salida or Path("resultados/pronostico" if suffix == "oct_dic" else "resultados/historico/cierre_modelos")
    days = pd.date_range(start, end + pd.Timedelta(hours=1), freq="D")
    items = [(station, day) for day in days for station in LOCATIONS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(lambda item: fetch_day(item, args.archivos), items))
    pieces = [frame for _, _, _, frame in files if frame is not None]
    if not pieces:
        raise ValueError("No se obtuvo ningún archivo diario de OpenAQ")
    raw = pd.concat(pieces, ignore_index=True)
    cleaned, audit = clean_readings(raw, start, end)
    if cleaned.empty:
        raise ValueError("OpenAQ no devolvió lecturas horarias válidas")
    statuses = pd.DataFrame([(station, day, status) for station, day, status, _ in files],
                            columns=[STATION, "fecha_objeto", "http"])
    if args.archivos is not None and (args.archivos / "estados.csv").is_file():
        log = pd.read_csv(args.archivos / "estados.csv", parse_dates=["fecha_objeto"], dtype={"http": str})
        log[STATION] = log.location_id.map({location: station for station, location in LOCATIONS.items()})
        log = log.set_index([STATION, "fecha_objeto"]).http
        missing = statuses.http.eq("sin_archivo")
        keys = pd.MultiIndex.from_frame(statuses.loc[missing, [STATION, "fecha_objeto"]])
        statuses.loc[missing, "http"] = keys.map(log).fillna("sin_archivo")
    args.datos.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(args.datos / filename, index=False, compression={"method": "gzip", "mtime": 1})
    audit.to_csv(output / f"eda_openaq_{suffix}_2024.csv", index=False, float_format="%.9g")
    statuses.groupby([STATION, "http"], dropna=False).size().rename("archivos").reset_index().to_csv(
        output / f"acceso_openaq_{suffix}_2024.csv", index=False,
    )
    print(audit[[STATION, "horas_con_medicion", "horas_sin_medicion", "cobertura", "mediana", "p95"]].to_string(index=False))


if __name__ == "__main__":
    main()
