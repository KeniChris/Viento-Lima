"""Predictores de detecciones térmicas cercanas y alineadas con viento IFS."""

import numpy as np
import pandas as pd

from .preparar import HOUR, complement_file, geographic_geometry, wind_components
from .viento import CALM_MS, DIRECTION, KEY, SPEED, STATION, STATIONS, TIME


DETECTIONS = "focos_calor_lima_entorno_2015_2024.csv"
FIRE_NEAR = ["focos_50km_24h", "focos_log_frp_50km_24h"]
FIRE_ALIGNED = ["focos_peso_llegada", "focos_log_frp_llegada"]
PUBLICATION_DELAY = 3 * HOUR
SEARCH_RADIUS_KM = 50


def load_detections(data_dir):
    columns = ["latitude", "longitude", "FECHA_HORA_UTC", "satellite", "confidence", "frp", "type"]
    with complement_file(data_dir, DETECTIONS) as source:
        fires = pd.read_csv(source, usecols=columns)
    utc = pd.to_datetime(fires["FECHA_HORA_UTC"], utc=True)
    fires[TIME] = utc.dt.tz_convert("America/Lima").dt.tz_localize(None)
    valid = fires["type"].eq(0) & fires.confidence.isin(["n", "h"]) & fires.frp.ge(0) & np.isfinite(fires.frp)
    valid &= fires.latitude.between(-90, 90) & fires.longitude.between(-180, 180) & fires[TIME].notna()
    return fires.loc[valid].drop_duplicates([TIME, "latitude", "longitude", "satellite"]).reset_index(drop=True)


def detection_wind(fires, wind):
    coordinates = (fires.latitude.to_numpy(), fires.longitude.to_numpy())
    distances = [geographic_geometry(coordinates, location)[0] for location in STATIONS.values()]
    nearest = np.asarray(list(STATIONS))[np.argmin(distances, axis=0)]
    wind_keys = pd.MultiIndex.from_arrays([nearest, fires[TIME].dt.floor("h")], names=KEY)
    matched = wind.set_index(KEY).reindex(wind_keys)
    speed = matched[SPEED].to_numpy()
    u, v = wind_components(np.where(speed >= CALM_MS, speed, np.nan), matched[DIRECTION].to_numpy())
    return speed, u, v


def fire_features(keys, fires, wind, horizon):
    coordinates = (fires.latitude.to_numpy(), fires.longitude.to_numpy())
    speed, u, v = detection_wind(fires, wind)
    acquired = pd.DatetimeIndex(fires[TIME])
    available = acquired + PUBLICATION_DELAY
    frp = fires.frp.to_numpy()
    features = []
    for receiver, emissions in keys.groupby(STATION, sort=False):
        times = pd.DatetimeIndex(emissions[TIME])
        if not times.is_monotonic_increasing:
            raise ValueError("Ordena las emisiones de cada estación antes de añadir focos")
        distance, east, north = geographic_geometry(coordinates, STATIONS[receiver])
        along = u * east + v * north
        alignment = along / np.maximum(speed, CALM_MS)
        count, heat, weighted_count, weighted_heat = np.zeros((4, len(times)))
        with_wind = np.zeros(len(times), dtype=bool)
        for i in np.flatnonzero(distance <= SEARCH_RADIUS_KM):
            first = times.searchsorted(available[i])
            last = times.searchsorted(available[i] + 24 * HOUR, side="right")
            if first == last:
                continue
            active = slice(first, last)
            count[active] += 1
            heat[active] += frp[i]
            if np.isfinite(speed[i]):
                with_wind[active] = True
            if alignment[i] < np.cos(np.pi / 4) or not np.isfinite(alignment[i]):
                continue
            hours = (times[active] - acquired[i]) / HOUR + horizon
            error = (3.6 * along[i] * hours - distance[i]) / max(2.5, .35 * distance[i])
            weight = np.where(np.abs(error) <= 2, alignment[i] * np.exp(-.5 * error**2), 0)
            weighted_count[active] += weight
            weighted_heat[active] += weight * frp[i]
        known = (count == 0) | with_wind
        features.append(emissions.assign(**{
            FIRE_NEAR[0]: count, FIRE_NEAR[1]: np.log1p(heat),
            FIRE_ALIGNED[0]: np.where(known, weighted_count, np.nan),
            FIRE_ALIGNED[1]: np.where(known, np.log1p(weighted_heat), np.nan),
        }))
    return pd.concat(features, ignore_index=True)
