"""Ejecuta el pronóstico histórico de PM2.5 y exporta la consulta para la web local."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .actualizar_aire import EXTENSION_START, PERU
from .modelos_finales import HOURLY_VERSIONS, TUNED_VERSION, features, load_bundle, load_sources, predict
from .preparar import CURRENT
from .publicar_pm25 import HOUR, demo_sources, lima_hour
from .viento import STATION, STATIONS, TIME, WIND_FILE


ROOT = Path(__file__).resolve().parents[1]
DEMO_ISSUE = "2024-11-15T07:00:00-05:00"


def consultation(sources, bundle, station, issue):
    target = issue + HOUR
    if target > pd.Timestamp.now(tz=PERU):
        raise ValueError("La hora objetivo debe haber transcurrido en el modo histórico")

    cutoff = issue.tz_localize(None)
    start = cutoff - 168 * HOUR
    air, weather, wind = [frame.loc[frame[TIME].between(start, cutoff)] for frame in sources]
    levels = air.loc[air[STATION].eq(station)].set_index(TIME)[CURRENT].sort_index()
    levels = levels.loc[np.isfinite(levels) & levels.ge(0)]
    hours = pd.date_range(issue - 23 * HOUR, issue, freq="h")
    observations = [
        {"fecha_hora": time.isoformat(), "pm25": float(value) if pd.notna(value) else None}
        for time, value in zip(hours, levels.reindex(hours.tz_localize(None)), strict=True)
    ]
    latest = None
    forecast = None
    if not levels.empty:
        last = levels.index[-1]
        latest = {
            "fecha_hora": last.tz_localize(PERU).isoformat(),
            "pm25": float(levels.iloc[-1]),
            "fuente": "SENAMHI" if last < EXTENSION_START else "SENAMHI vía OpenAQ",
        }
    if cutoff in levels.index:
        frame = features(air, weather, wind, 1, require_target=False)
        rows = frame.loc[frame[TIME].eq(cutoff) & frame[STATION].eq(station)]
        if not rows.empty:
            estimate = float(predict(None, bundle, rows).item())
            if not np.isfinite(estimate):
                raise ValueError("El modelo produjo un pronóstico inválido")
            forecast = {
                "emitido_en": issue.isoformat(),
                "objetivo_en": target.isoformat(),
                "calculado_en": pd.Timestamp.now(tz=PERU).floor("s").isoformat(),
                "horizonte_h": 1,
                "pm25_estimado": estimate,
                "version_modelo": bundle[1]["version"],
            }
    return {
        "estacion": station,
        "zona_horaria": PERU,
        "unidad": "µg/m³",
        "modo": "HISTORICO",
        "estado": "HISTORICO" if forecast else "SIN_PRONOSTICO",
        "ultima_medicion": latest,
        "pronostico": forecast,
        "observaciones": observations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estacion", choices=STATIONS, default="SAN_BORJA")
    parser.add_argument("--emision", default=DEMO_ISSUE, help="Hora de emisión de Lima con desfase explícito")
    parser.add_argument("--version", choices=HOURLY_VERSIONS, default=TUNED_VERSION)
    parser.add_argument("--modelos", type=Path, default=ROOT / "modelos")
    parser.add_argument("--datos", type=Path, help="Directorio de insumos del proyecto")
    parser.add_argument("--descargas-clima", type=Path, help="Directorio de CSV originales de ERA5 e IFS")
    parser.add_argument("--salida", type=Path, help="Ruta del JSON en UTF-8")
    args = parser.parse_args()
    if args.descargas_clima and not args.datos:
        parser.error("--descargas-clima requiere --datos")
    try:
        issue = lima_hour(args.emision)
        if args.datos:
            sources = load_sources(args.datos, args.datos / WIND_FILE, args.descargas_clima)
        else:
            sources = demo_sources(issue)
        bundle = load_bundle(None, args.modelos, 1, version=args.version)
        result = consultation(sources, bundle, args.estacion, issue)
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.salida:
            args.salida.write_text(content, encoding="utf-8")
        else:
            print(content, end="")
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
