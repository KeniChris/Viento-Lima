"""Descarga y validación de viento histórico IFS a 10 m para Lima."""

import argparse
from io import StringIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd

STATION = "ESTACION"
TIME = "FECHA_HORA_LIMA"
KEY = [STATION, TIME]
SPEED = "ifs10_velocidad_ms"
DIRECTION = "ifs10_direccion_desde_grados"
WIND_FILE = "ifs_viento_10m_lima.csv.gz"
STATIONS = {
    "CAMPO_DE_MARTE": (-12.0705, -77.0432),
    "CARABAYLLO": (-11.9022, -77.0336),
    "SANTA_ANITA": (-12.0430, -76.9714),
    "SAN_BORJA": (-12.1086, -77.0077),
    "SAN_JUAN_DE_LURIGANCHO": (-11.9816, -76.9992),
    "SAN_MARTIN_DE_PORRES": (-12.0089, -77.0845),
    "VILLA_MARIA_DEL_TRIUNFO": (-12.1664, -76.9200),
}
INTERVALS = (
    ("2017-01-01", "2018-12-31"),
    ("2019-01-01", "2019-12-31"),
    ("2020-01-01", "2020-12-31"),
    ("2021-01-01", "2022-12-31"),
    ("2023-01-01", "2024-04-30"),
)
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
CSV_COLUMNS = {"time": TIME, "wind_speed_10m (m/s)": SPEED, "wind_direction_10m (°)": DIRECTION}
CALM_MS = 0.5


def request_url(start, end, model="ecmwf_ifs", hourly="wind_speed_10m,wind_direction_10m"):
    latitudes, longitudes = zip(*STATIONS.values())
    params = {
        "latitude": ",".join(map(str, latitudes)),
        "longitude": ",".join(map(str, longitudes)),
        "start_date": start,
        "end_date": end,
        "hourly": hourly,
        "models": model,
        "timezone": "America/Lima",
        "wind_speed_unit": "ms",
        "format": "csv",
    }
    return f"{ARCHIVE}?{urlencode(params)}"


def validate_hours(frame, source):
    if frame[KEY].isna().any().any() or not frame[TIME].eq(frame[TIME].dt.floor("h")).all():
        raise ValueError(f"{source} contiene claves inválidas o fuera del calendario horario")
    if frame.duplicated(KEY).any():
        raise ValueError(f"{source} contiene claves estación-hora duplicadas")


def parse_wind_csv(content, start, end):
    metadata_csv, hourly_csv = content.replace("\r\n", "\n").split("\n\n", maxsplit=1)
    metadata = pd.read_csv(StringIO(metadata_csv))
    names = dict(enumerate(STATIONS))
    if (
        metadata["location_id"].tolist() != list(names)
        or not metadata.timezone.eq("America/Lima").all()
        or not metadata.utc_offset_seconds.eq(-18000).all()
    ):
        raise ValueError("La respuesta IFS cambió el orden de ubicaciones o la zona horaria")
    metadata[STATION] = metadata.location_id.map(names)
    metadata = metadata.rename(columns={"latitude": "ifs10_lat_celda", "longitude": "ifs10_lon_celda"})
    requested = pd.DataFrame.from_dict(STATIONS, orient="index", columns=["lat", "lon"])
    displacement = metadata.set_index(STATION)[["ifs10_lat_celda", "ifs10_lon_celda"]].to_numpy()
    if not (np.abs(displacement - requested.to_numpy()) < 0.12).all():
        raise ValueError("Una celda devuelta está demasiado lejos de la coordenada solicitada")
    hourly = pd.read_csv(StringIO(hourly_csv), usecols=["location_id", *CSV_COLUMNS])
    hourly = hourly.rename(columns=CSV_COLUMNS)
    hourly[TIME] = pd.to_datetime(hourly[TIME], format="%Y-%m-%dT%H:%M", errors="raise")
    hourly = hourly.merge(
        metadata[["location_id", STATION, "ifs10_lat_celda", "ifs10_lon_celda"]],
        on="location_id",
        how="left",
        validate="many_to_one",
    ).drop(columns="location_id")
    validate_hours(hourly, "IFS a 10 m")
    start_hour = pd.Timestamp(start)
    end_hour = pd.Timestamp(end) + pd.Timedelta(hours=23)
    if not hourly[TIME].between(start_hour, end_hour).all():
        raise ValueError("La respuesta IFS contiene horas fuera del intervalo solicitado")
    counts = hourly.groupby(STATION).size().reindex(STATIONS)
    if not counts.eq(len(pd.date_range(start_hour, end_hour, freq="h"))).all():
        raise ValueError("La respuesta IFS tiene horas ausentes para alguna estación")
    return hourly


def clean_wind(frame):
    speed = pd.to_numeric(frame[SPEED], errors="coerce")
    angle = pd.to_numeric(frame[DIRECTION], errors="coerce")
    invalid_speed = frame[SPEED].notna() & ~(np.isfinite(speed) & speed.ge(0))
    invalid_angle = frame[DIRECTION].notna() & ~angle.between(0, 360)
    frame[SPEED] = speed.mask(invalid_speed)
    frame[DIRECTION] = angle.mask(invalid_angle).mod(360)
    return {"velocidad_invalida": int(invalid_speed.sum()), "direccion_invalida": int(invalid_angle.sum())}


def build_wind_file(output, downloads=None):
    pieces = []
    for start, end in INTERVALS:
        if downloads is None:
            with urlopen(request_url(start, end), timeout=90) as response:
                payload = response.read().decode("utf-8-sig")
        else:
            payload = (downloads / f"{start}_{end}.csv").read_text(encoding="utf-8-sig")
        pieces.append(parse_wind_csv(payload, start, end))
    wind = pd.concat(pieces, ignore_index=True).sort_values(KEY)
    validate_hours(wind, "archivo IFS consolidado")
    report = clean_wind(wind)
    output.parent.mkdir(parents=True, exist_ok=True)
    wind[KEY + [SPEED, DIRECTION, "ifs10_lat_celda", "ifs10_lon_celda"]].to_csv(
        output, index=False, compression={"method": "gzip", "mtime": 1}
    )
    return len(wind), report


def load_wind(path):
    frame = pd.read_csv(path, parse_dates=[TIME])
    validate_hours(frame, "archivo IFS a 10 m")
    if set(frame[STATION]) != set(STATIONS):
        raise ValueError("El archivo IFS a 10 m debe cubrir las siete estaciones")
    report = clean_wind(frame)
    return frame, report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=Path, default=Path("datos") / WIND_FILE)
    parser.add_argument("--descargas", type=Path, help="CSV de la API ya descargados, nombrados por intervalo")
    args = parser.parse_args()
    rows, audit = build_wind_file(args.salida, args.descargas)
    print(f"{rows:,} horas-estación validadas → {args.salida}")
    print(audit)
