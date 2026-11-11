"""Contrasta el reloj del CSV de SENAMHI y descarga su continuación en OpenAQ."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pandas as pd

from .viento import STATION, TIME

ARCHIVE = "https://openaq-data-archive.s3.amazonaws.com"
PREFIX = "records/csv.gz"
PERU = "America/Lima"
UTC_TIME = "FECHA_HORA_UTC"
VALUE = "PM2_5"
UNIT = "µg/m³"
LOCATIONS = {
    "CAMPO_DE_MARTE": 2415,
    "SAN_BORJA": 2402,
    "SAN_JUAN_DE_LURIGANCHO": 7464,
    "SAN_MARTIN_DE_PORRES": 2351,
}
CHECK_DAYS = ("2023-12-15", "2024-03-15", "2024-04-15")
EXTENSION_START = pd.Timestamp("2024-05-01")
EXTENSION_END = pd.Timestamp("2024-06-30 23:00:00")
EXTENSION_FILE = "pm25_senamhi_openaq_may_jun_2024.csv.gz"


def read_public(url):
    with urlopen(url, timeout=40) as response:
        return response.read()


def daily_key(location_id, date):
    date = pd.Timestamp(date)
    return f"{PREFIX}/locationid={location_id}/year={date:%Y}/month={date:%m}/location-{location_id}-{date:%Y%m%d}.csv.gz"


def read_pm25(archive_item):
    station, key = archive_item
    try:
        content = read_public(f"{ARCHIVE}/{key}")
    except HTTPError as exc:
        if exc.code not in (404, 503):
            raise
        return station, key, exc.code, None
    frame = pd.read_csv(BytesIO(content), compression="gzip")
    frame = frame.loc[frame.parameter.eq("pm25") & frame.units.eq(UNIT), ["location_id", "datetime", "value"]]
    if not frame.location_id.eq(LOCATIONS[station]).all():
        raise ValueError(f"Ubicación inesperada en {key}")
    utc = pd.to_datetime(frame.datetime, format="ISO8601", utc=True)
    frame[UTC_TIME] = utc
    frame[TIME] = utc.dt.tz_convert(PERU).dt.tz_localize(None)
    frame[STATION] = station
    frame["archivo_openaq"] = f"{ARCHIVE}/{key}"
    frame = frame.rename(columns={"value": VALUE, "datetime": "FECHA_HORA_OPENAQ"})
    return station, key, 200, frame.loc[utc.eq(utc.dt.floor("h")) & frame[VALUE].ge(0)]


def downloaded_readings(items):
    with ThreadPoolExecutor(max_workers=8) as workers:
        files = list(workers.map(read_pm25, items))
    pieces = [frame for _, _, _, frame in files if frame is not None]
    if not pieces:
        raise ValueError("Ningún archivo de OpenAQ devolvió datos")
    frame = pd.concat(pieces, ignore_index=True).drop_duplicates([STATION, UTC_TIME, VALUE])
    ambiguous = frame.duplicated([STATION, UTC_TIME], keep=False)
    downloads = pd.DataFrame([(station, key, status) for station, key, status, _ in files],
                             columns=[STATION, "objeto_openaq", "http"])
    return frame.loc[~ambiguous].copy(), int(ambiguous.sum()), downloads


def verify_clock(raw_path, output_dir):
    raw_path = Path(raw_path)
    if not raw_path.is_file():
        raise FileNotFoundError(
            f"Falta {raw_path}. Copia el CSV del Hito 2 a datos/ o indica su carpeta con --datos."
        )
    items = [(station, daily_key(location, day))
             for station, location in LOCATIONS.items() for day in CHECK_DAYS]
    public, _, _ = downloaded_readings(items)
    raw = pd.read_csv(raw_path, usecols=[STATION, "FECHA", "HORA", VALUE])
    raw["HORA_EN_CSV"] = pd.to_datetime(
        raw.FECHA.astype(str) + raw.HORA.astype(str).str.zfill(6), format="%Y%m%d%H%M%S", utc=True
    )
    raw = raw.dropna(subset=[VALUE]).rename(columns={VALUE: "pm25_csv_senamhi"})
    raw = raw[[STATION, "HORA_EN_CSV", "pm25_csv_senamhi"]]
    merged = []
    for offset, label in ((0, "UTC"), (5, "America/Lima")):
        current = raw.assign(**{UTC_TIME: raw.HORA_EN_CSV + pd.Timedelta(hours=offset)})
        matched = public.merge(current, on=[STATION, UTC_TIME], how="left", validate="one_to_one")
        matched["lectura_horaria_csv"] = label
        matched["coincide_exacto"] = matched[VALUE].eq(matched.pm25_csv_senamhi)
        # OpenAQ publica estos valores con dos decimales y el CSV conserva más precisión.
        matched["coincide"] = matched[VALUE].sub(matched.pm25_csv_senamhi).abs().le(.005000001)
        merged.append(matched)
    pairs = pd.concat(merged, ignore_index=True)
    summary = pairs.groupby(["lectura_horaria_csv", STATION], as_index=False).agg(
        registros_openaq=(VALUE, "size"), pares=("pm25_csv_senamhi", "count"),
        coincidencias=("coincide", "sum"), coincidencias_exactas=("coincide_exacto", "sum"),
    )
    summary["unidad_openaq"] = UNIT
    totals = summary.groupby("lectura_horaria_csv")[["pares", "coincidencias"]].sum()
    if totals.loc["UTC", "pares"] < 24 or totals.loc["UTC", "coincidencias"] / totals.loc["UTC", "pares"] < .95:
        raise ValueError("La muestra no confirma la hora UTC del CSV")
    if totals.loc["America/Lima", "coincidencias"] >= totals.loc["UTC", "coincidencias"]:
        raise ValueError("Las dos referencias horarias no se distinguen en la muestra")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_dir / "verificacion_horaria.csv", index=False)
    pairs.loc[pairs["lectura_horaria_csv"].eq("UTC") & pairs["coincide"]].groupby(STATION).head(4).to_csv(
        output_dir / "ejemplos_hora_verificada.csv", index=False
    )
    print(summary.to_string(index=False))


def download_extension(data_dir, output_dir):
    days = pd.date_range(EXTENSION_START - pd.Timedelta(days=1), EXTENSION_END, freq="D")
    keys = [(station, daily_key(location, date)) for station, location in LOCATIONS.items() for date in days]
    print(f"Archivos diarios consultados: {len(keys)}", flush=True)
    air, ambiguous, downloads = downloaded_readings(keys)
    air = air.loc[air[TIME].between(EXTENSION_START, EXTENSION_END)].sort_values([STATION, TIME])
    if air.empty:
        raise ValueError("Los archivos obtenidos no contienen PM2.5 horario del período solicitado")
    data_dir.mkdir(parents=True, exist_ok=True)
    air[[STATION, TIME, UTC_TIME, VALUE]].to_csv(
        data_dir / EXTENSION_FILE, index=False, compression={"method": "gzip", "mtime": 1}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    downloads.groupby([STATION, "http"]).size().rename("archivos").reset_index().to_csv(
        output_dir / "acceso_openaq_2024.csv", index=False
    )
    coverage = air.groupby(STATION, as_index=False).agg(
        registros=(VALUE, "size"), primera_hora_lima=(TIME, "min"), ultima_hora_lima=(TIME, "max")
    )
    coverage["location_id_openaq"] = coverage[STATION].map(LOCATIONS)
    coverage["unidad"] = UNIT
    coverage.to_csv(output_dir / "cobertura_openaq_2024.csv", index=False)
    print(f"{len(air)} mediciones horarias nuevas. Filas con claves ambiguas excluidas: {ambiguous}.")
    print(coverage.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--salida", type=Path, default=Path("resultados"))
    parser.add_argument("--extender", action="store_true")
    args = parser.parse_args()
    try:
        verify_clock(args.datos / "aire_lima_completo.csv", args.salida)
    except FileNotFoundError as error:
        parser.error(str(error))
    if args.extender:
        download_extension(args.datos, args.salida)
