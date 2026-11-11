"""Reconstruye la matriz del Hito 2 y añade predictores espaciales para t+1."""

import argparse
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from .viento import CALM_MS, DIRECTION, KEY, SPEED, STATION, STATIONS, TIME, WIND_FILE
from .viento import load_wind, validate_hours

TARGET = "objetivo_pm25_t_mas_1h"
TARGET_TIME = "FECHA_OBJETIVO"
CURRENT = "PM2_5"
HOUR = pd.Timedelta(hours=1)
METEO = [
    "temperature_2m", "relative_humidity_2m", "precipitation", "pressure_msl",
    "wind_speed_10m", "wind_direction_10m",
]
LOCAL_BASE = [
    CURRENT, "pm25_lag_1h", "pm25_lag_24h", "pm25_media_6h", *METEO[:-1],
    "viento_seno", "viento_coseno", "hora_seno", "hora_coseno", "mes_seno",
    "mes_coseno", "dia_semana", "fin_de_semana", "anio", "lluvia_en_t", STATION,
]
HISTORY = ["pm25_lag_3h", "pm25_std_6h", "pm25_amplitud_6h", "pm25_cambio_1h", "pm25_cambio_24h"]
WEATHER_100M = [
    "wind_speed_100m", "viento_u_100m", "viento_v_100m", "shortwave_radiation",
    "cloud_cover", "cloud_cover_low", "surface_pressure", "dew_point_2m",
]
WEATHER_EXTRA = [f"{model}_{name}" for model in ("era5", "ifs") for name in WEATHER_100M] + ["ifs_disponible"]
FIRMS = [
    "firms_detecciones_vegetacion_25km_24h", "firms_detecciones_vegetacion_50km_24h",
    "firms_frp_suma_25km_24h", "firms_frp_suma_50km_24h", "firms_ventana_archivo_disponible_24h",
]
VEHICLES = [
    "hogares_con_auto_camioneta_2017", "porcentaje_hogares_con_auto_camioneta_2017",
    "hogares_con_motocicleta_2017", "porcentaje_hogares_con_motocicleta_2017",
]
CENSUS = ["censo_poblacion_2017", "censo_pct_0_4_2017", "censo_pct_65_mas_2017"]
HITO2 = LOCAL_BASE + HISTORY + WEATHER_EXTRA + [
    "firms_detecciones_vegetacion_25km_24h_retardo3h", "firms_detecciones_vegetacion_50km_24h_retardo3h",
    "firms_log_frp_25km_24h_retardo3h", "firms_log_frp_50km_24h_retardo3h",
    "firms_ventana_archivo_disponible_24h_retardo3h",
] + CENSUS + VEHICLES
WIND = [SPEED, "ifs10_u_ms", "ifs10_v_ms", "ifs10_calma", "ifs10_disponible"]
NEIGHBOR_LAGS = (0, 1, 3, 5)
NEIGHBORS = [f"vecinos_{metric}_{lag}h" for lag in NEIGHBOR_LAGS for metric in ("media", "max", "n")]
TRANSPORT = [
    "transporte_pm25_media", "transporte_pm25_max", "transporte_origenes",
    "transporte_rezago_media_h", "transporte_peso_total",
]
FEATURES = {
    "base": HITO2,
    "viento": HITO2 + WIND,
    "vecinos": HITO2 + WIND + NEIGHBORS,
    "transporte": HITO2 + WIND + NEIGHBORS + TRANSPORT,
}
COMPLEMENT_ARCHIVE = "csv_complementarios_pm25_lima.zip"
COMPLEMENT_SERIES = "datos_complementarios_pm25_2015_2024.csv"
VEHICLE_CONTEXT = "tenencia_vehicular_7_distritos_censo2017.csv"


def local_complement(data_dir, filename):
    for folder in (data_dir, data_dir / "csv_complementarios_pm25_lima", data_dir / "datos_complementarios_pm25"):
        candidate = folder / filename
        if candidate.is_file():
            return candidate
    return None


@contextmanager
def complement_file(data_dir, filename):
    candidate = local_complement(data_dir, filename)
    if candidate is not None:
        with candidate.open("rb") as stream:
            yield stream
        return
    with ZipFile(data_dir / COMPLEMENT_ARCHIVE) as archive:
        names = [name for name in archive.namelist() if Path(name).name == filename]
        if len(names) != 1:
            raise ValueError(f"Se necesita exactamente un archivo {filename} en el ZIP")
        with archive.open(names[0]) as stream:
            yield stream


def load_air(data_dir):
    air = pd.read_csv(
        data_dir / "aire_lima_completo.csv",
        usecols=[STATION, "FECHA", "HORA", CURRENT],
        dtype={STATION: "string", "FECHA": "string", "HORA": "string"},
    )
    air[STATION] = air[STATION].str.strip()
    raw_time = air.pop("FECHA").str.strip().str.zfill(8) + air.pop("HORA").str.strip().str.zfill(6)
    # La comparación con lecturas fechadas de OpenAQ confirma el huso del CSV original.
    utc = pd.to_datetime(raw_time, format="%Y%m%d%H%M%S", errors="coerce", utc=True)
    air["FECHA_HORA_UTC"] = utc
    air[TIME] = utc.dt.tz_convert("America/Lima").dt.tz_localize(None)
    missing_keys = int(air[KEY].isna().any(axis=1).sum())
    air = air.dropna(subset=KEY)
    validate_hours(air, "SENAMHI")
    if set(air[STATION]) != set(STATIONS):
        raise ValueError("Las estaciones de SENAMHI no coinciden con el archivo del proyecto")
    numeric = pd.to_numeric(air[CURRENT], errors="coerce")
    invalid = air[CURRENT].notna() & ~(np.isfinite(numeric) & numeric.ge(0))
    air[CURRENT] = numeric.mask(invalid)
    audit = {"horas_aire_original": len(air) + missing_keys, "zona_original_aire": "UTC",
             "zona_modelado": "America/Lima", "unidad_pm25": "µg/m³",
             "inicio_utc": utc.min(), "fin_utc": utc.max(),
             "inicio_lima": air[TIME].min(), "fin_lima": air[TIME].max(),
             "claves_aire_invalidas": missing_keys,
             "pm25_invalidos": int(invalid.sum()), "pm25_sin_medicion": int(air[CURRENT].isna().sum())}
    return air, audit


def local_features(air, meteo):
    meteo = meteo.copy()
    meteo[STATION] = meteo[STATION].str.strip()
    validate_hours(meteo, "clima original")
    limits = {"relative_humidity_2m": (0, 100), "precipitation": (0, np.inf),
              "pressure_msl": (np.finfo(float).tiny, np.inf), "wind_speed_10m": (0, np.inf),
              "wind_direction_10m": (0, 360)}
    for name, (low, high) in limits.items():
        meteo[name] = meteo[name].where(meteo[name].between(low, high))
    frame = air.merge(meteo, how="left", on=KEY, validate="one_to_one").sort_values(KEY)
    station = frame.groupby(STATION, sort=False)
    time, pm = frame[TIME], frame[CURRENT]
    frame[TARGET] = station[CURRENT].shift(-1).where(station[TIME].shift(-1).sub(time).eq(HOUR))
    frame[TARGET_TIME] = time + HOUR
    for lag in (1, 3, 23, 24):
        name = f"pm25_lag_{lag}h" if lag != 23 else "pm25_estacional_24h"
        frame[name] = station[CURRENT].shift(lag).where(time.sub(station[TIME].shift(lag)).eq(lag * HOUR))
    consecutive = time.sub(station[TIME].shift(5)).eq(5 * HOUR)
    rolled = station[CURRENT].rolling(6, min_periods=6)
    frame["pm25_media_6h"] = np.where(consecutive, rolled.mean().to_numpy(), np.nan)
    frame["pm25_std_6h"] = np.where(consecutive, rolled.std().to_numpy(), np.nan)
    amplitude = (rolled.max() - rolled.min()).to_numpy()
    frame["pm25_amplitud_6h"] = np.where(consecutive, amplitude, np.nan)
    frame["pm25_cambio_1h"] = pm - frame["pm25_lag_1h"]
    frame["pm25_cambio_24h"] = pm - frame["pm25_lag_24h"]
    frame["anio"], frame["dia_semana"] = time.dt.year, time.dt.dayofweek
    frame["fin_de_semana"] = frame["dia_semana"].ge(5)
    frame["hora_seno"] = np.sin(2 * np.pi * time.dt.hour / 24)
    frame["hora_coseno"] = np.cos(2 * np.pi * time.dt.hour / 24)
    frame["mes_seno"] = np.sin(2 * np.pi * time.dt.month / 12)
    frame["mes_coseno"] = np.cos(2 * np.pi * time.dt.month / 12)
    frame["viento_seno"] = np.sin(np.deg2rad(frame["wind_direction_10m"]))
    frame["viento_coseno"] = np.cos(np.deg2rad(frame["wind_direction_10m"]))
    frame["lluvia_en_t"] = frame.precipitation.gt(0).where(frame.precipitation.notna())
    # Mantener la misma cohorte que el Hito 2 para comparar todos los experimentos.
    return frame.dropna(subset=LOCAL_BASE + [TARGET]).drop(columns="wind_direction_10m").copy()


def add_hito2_sources(frame, data_dir):
    with complement_file(data_dir, COMPLEMENT_SERIES) as stream:
        extra = pd.read_csv(stream, usecols=KEY + WEATHER_EXTRA + FIRMS, parse_dates=[TIME])
    validate_hours(extra, "complementos del Hito 2")
    weather = frame.merge(extra[KEY + WEATHER_EXTRA], on=KEY, how="left", validate="one_to_one", indicator=True)
    if not weather.pop("_merge").eq("both").all():
        raise ValueError("Faltan claves del complemento horario del Hito 2")
    fires = extra[KEY + FIRMS].copy()
    fires[TIME] += 3 * HOUR
    for radius in (25, 50):
        name = f"firms_frp_suma_{radius}km_24h"
        fires[f"firms_log_frp_{radius}km_24h"] = np.log1p(fires.pop(name))
    fires = fires.rename(columns={name: f"{name}_retardo3h" for name in fires if name not in KEY})
    weather = weather.merge(fires, on=KEY, how="left", validate="one_to_one")
    census = pd.read_csv(data_dir / "poblacion_censo2017_7_distritos.csv")
    census[CENSUS[0]] = census.POBLACION_CENSADA_2017
    census[CENSUS[1]] = 100 * census.EDAD_0_4_2017 / census.POBLACION_CENSADA_2017
    census[CENSUS[2]] = 100 * census.EDAD_65_MAS_2017 / census.POBLACION_CENSADA_2017
    with complement_file(data_dir, VEHICLE_CONTEXT) as stream:
        vehicles = pd.read_csv(stream, usecols=[STATION] + VEHICLES)
    weather = weather.merge(census[[STATION] + CENSUS], on=STATION, how="left", validate="many_to_one")
    weather = weather.merge(vehicles, on=STATION, how="left", validate="many_to_one")
    if weather[CENSUS + VEHICLES].isna().any().any():
        raise ValueError("Faltan datos del Censo 2017 o vehículos para alguna estación")
    return weather


def wind_components(speed, from_degrees):
    angle = np.deg2rad(from_degrees)
    return -speed * np.sin(angle), -speed * np.cos(angle)


def station_geometry(origin, destination):
    return geographic_geometry(STATIONS[origin], STATIONS[destination])


def geographic_geometry(origin, destination):
    lat1, lon1 = np.deg2rad(origin)
    lat2, lon2 = np.deg2rad(destination)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    distance_km = 2 * 6371.0088 * np.arcsin(np.sqrt(a))
    east = np.sin(dlon) * np.cos(lat2)
    north = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    bearing = np.arctan2(east, north)
    return distance_km, np.sin(bearing), np.cos(bearing)


def weighted_average(values, weights):
    total = weights.sum(axis=0)
    average = np.full(total.shape, np.nan)
    np.divide(np.nansum(values * weights, axis=0), total, out=average, where=total > 0)
    return average


def max_observed(values, valid):
    return np.where(valid.any(axis=0), np.max(np.where(valid, values, -np.inf), axis=0), np.nan)


def spatial_features(air, wind):
    calendar = pd.date_range(air[TIME].min(), air[TIME].max(), freq="h")

    def matrix(frame, column):
        return frame.pivot(index=TIME, columns=STATION, values=column).reindex(index=calendar, columns=STATIONS)

    pm = matrix(air, CURRENT)
    speed = matrix(wind, SPEED)
    direction = matrix(wind, DIRECTION)
    has_direction = speed.ge(CALM_MS) & direction.notna()
    u, v = wind_components(speed.where(has_direction), direction)
    history = {lag: pm.shift(lag).to_numpy() for lag in range(6)}
    u_history = {lag: u.shift(lag).to_numpy() for lag in history}
    v_history = {lag: v.shift(lag).to_numpy() for lag in history}
    speed_history = {lag: speed.shift(lag).to_numpy() for lag in history}
    direction_history = {lag: direction.shift(lag).to_numpy() for lag in history}
    result = []
    geometry = []
    for receiver in STATIONS:
        sources = [source for source in STATIONS if source != receiver]
        source_idx = [pm.columns.get_loc(source) for source in sources]
        distances = np.array([station_geometry(source, receiver) for source in sources])
        rows = pd.DataFrame({STATION: receiver, TIME: calendar, SPEED: speed[receiver].to_numpy()})
        rows["ifs10_u_ms"], rows["ifs10_v_ms"] = u[receiver].to_numpy(), v[receiver].to_numpy()
        rows["ifs10_calma"] = np.where(speed[receiver].notna(), speed[receiver].lt(CALM_MS), np.nan)
        rows["ifs10_disponible"] = (speed[receiver].notna() & direction[receiver].notna()).to_numpy(dtype=int)

        # Las mismas observaciones vecinas sin usar dirección forman el control de ablación.
        for lag in NEIGHBOR_LAGS:
            values = history[lag][:, source_idx].T
            valid = np.isfinite(values)
            rows[f"vecinos_media_{lag}h"] = weighted_average(values, valid / distances[:, 0, None])
            rows[f"vecinos_max_{lag}h"] = max_observed(values, valid)
            rows[f"vecinos_n_{lag}h"] = valid.sum(axis=0)

        n = len(calendar)
        best_weight = np.zeros((len(sources), n))
        best_pm = np.full((len(sources), n), np.nan)
        best_lag = np.full((len(sources), n), np.nan)
        has_any_wind = np.zeros(n, dtype=bool)
        for j, (source, col, (distance, east, north)) in enumerate(zip(sources, source_idx, distances)):
            geometry.append({ "origen": source, "destino": receiver, "distancia_km": distance,
                              "rumbo_grados": np.degrees(np.arctan2(east, north)) % 360})
            for lag, past_pm in history.items():
                past_speed = speed_history[lag][:, col]
                available = np.isfinite(past_speed) & np.isfinite(direction_history[lag][:, col])
                has_any_wind |= available
                along = u_history[lag][:, col] * east + v_history[lag][:, col] * north
                alignment = along / np.maximum(past_speed, CALM_MS)
                travel_km = along * 3.6 * (lag + 1)
                relative_error = np.abs(travel_km - distance) / max(2.5, 0.35 * distance)
                valid = (alignment >= np.cos(np.pi / 4)) & (relative_error <= 2) & np.isfinite(past_pm[:, col])
                weight = np.where(valid, alignment * np.exp(-0.5 * relative_error**2) / distance, 0)
                better = weight > best_weight[j]
                best_weight[j, better] = weight[better]
                best_pm[j, better] = past_pm[better, col]
                best_lag[j, better] = lag

        rows["transporte_pm25_media"] = weighted_average(best_pm, best_weight)
        rows["transporte_pm25_max"] = max_observed(best_pm, best_weight > 0)
        rows["transporte_origenes"] = np.where(has_any_wind, (best_weight > 0).sum(axis=0), np.nan)
        rows["transporte_rezago_media_h"] = weighted_average(best_lag, best_weight)
        rows["transporte_peso_total"] = np.where(has_any_wind, best_weight.sum(axis=0), np.nan)
        result.append(rows)
    return pd.concat(result, ignore_index=True), pd.DataFrame(geometry)


def prepare_dataset(data_dir, wind_path, output_dir):
    required = [
        data_dir / "aire_lima_completo.csv",
        data_dir / "clima_era5_7_estaciones.parquet",
        data_dir / "poblacion_censo2017_7_distritos.csv",
        wind_path,
    ]
    missing = [path for path in required if not path.is_file()]
    archive = data_dir / COMPLEMENT_ARCHIVE
    if not archive.is_file() and any(
        local_complement(data_dir, filename) is None for filename in (COMPLEMENT_SERIES, VEHICLE_CONTEXT)
    ):
        missing.append(archive)
    if missing:
        listing = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(
            f"Faltan archivos para preparar la matriz:\n{listing}\n"
            "Copia los insumos del equipo a datos/ o indica su carpeta con --datos. "
            "Genera el archivo de viento con python -m codigo.viento o indica su ruta con --viento."
        )
    air, air_audit = load_air(data_dir)
    wind, wind_audit = load_wind(wind_path)
    meteo = pd.read_parquet(data_dir / "clima_era5_7_estaciones.parquet", columns=KEY + METEO)
    base = add_hito2_sources(local_features(air, meteo), data_dir)
    extra, geometry = spatial_features(air, wind)
    frame = base.merge(extra, on=KEY, how="left", validate="one_to_one", indicator=True)
    if len(frame) != len(base) or not frame.pop("_merge").eq("both").all():
        raise ValueError("La unión espacial cambió la cohorte de evaluación")
    features = FEATURES["transporte"]
    numeric = [name for name in features if name != STATION]
    if len(features) != len(set(features)) or {TARGET, TARGET_TIME, TIME, DIRECTION}.intersection(features):
        raise ValueError("El inventario de predictores contiene columnas repetidas o prohibidas")
    frame[numeric] = frame[numeric].astype(float)
    frame = frame[KEY + [TARGET_TIME, TARGET, "pm25_estacional_24h"] + numeric].sort_values(
        [TIME, STATION], ignore_index=True
    )
    matched = air[KEY].merge(wind, on=KEY, how="left", validate="one_to_one")
    audit = matched.assign(
        anio=matched[TIME].dt.year, viento_valido=matched[SPEED].notna() & matched[DIRECTION].notna(),
        calma=matched[SPEED].lt(CALM_MS),
    ).groupby([STATION, "anio"], as_index=False).agg(
        horas=(TIME, "size"), horas_con_viento=("viento_valido", "sum"), horas_calma=("calma", "sum"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_dir / "matriz_hito3.parquet", index=False)
    air[KEY + ["FECHA_HORA_UTC", CURRENT]].sort_values(KEY).to_parquet(
        output_dir / "aire_senamhi_normalizado.parquet", index=False
    )
    audit.to_csv(output_dir / "cobertura_viento.csv", index=False)
    geometry.to_csv(output_dir / "distancias_y_rumbos.csv", index=False)
    pd.DataFrame.from_records([{
        **air_audit, "filas_ifs10": len(wind), **wind_audit,
        "estaciones": len(STATIONS), "celdas_ifs10": wind[["ifs10_lat_celda", "ifs10_lon_celda"]].drop_duplicates().shape[0],
        "viento_no_nulo": int(matched[SPEED].notna().sum()), "filas_modelado_antes_de_purga": len(frame),
        "filas_modelado_con_viento": int(frame["ifs10_disponible"].sum()),
        "filas_modelado_con_origen_alineado": int(frame["transporte_origenes"].gt(0).sum()),
    }]).to_csv(output_dir / "auditoria_preparacion.csv", index=False)
    return frame


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", type=Path, default=Path("datos"))
    parser.add_argument("--viento", type=Path, default=Path("datos") / WIND_FILE)
    parser.add_argument("--salida", type=Path, default=Path("resultados"))
    args = parser.parse_args()
    try:
        model_data = prepare_dataset(args.datos, args.viento, args.salida)
    except FileNotFoundError as error:
        parser.error(str(error))
    print(f"{len(model_data):,} filas preparadas, {len(FEATURES['transporte'])} predictores en el grupo completo")
