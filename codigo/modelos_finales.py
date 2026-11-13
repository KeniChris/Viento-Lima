"""Entrenamiento, persistencia e inferencia de los pronósticos horarios."""

from dataclasses import dataclass, replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from xgboost import XGBRegressor

from .analizar_mae import GROUP_BY_HOUR, HISTORY, historical_features
from .evaluar_lstm import fit_lstm, lstm_network, predict_lstm_sequence
from .preparar_lecturas_recientes import END, FILENAME, START, TEST_END, TEST_FILE
from .preparar_lecturas_recientes import TEST_START as HOLDOUT_START, load_clean
from .pronosticar import CURRENT, GROUPS, TARGET, build_matrix, candidates, load_hourly_sources, make_regressor
from .viento import KEY, STATION, validate_hours


MODELS = {1: "xgb_historia_mae", 3: "lstm_24h", 6: "lstm_24h"}
VERSION = "pm25-1"


@dataclass(frozen=True)
class HourlyTransform:
    columns: list[str]
    medians: list[float]
    indicators: list[int]
    stations: list[str]
    feature_names: list[str]
    mean: list[float] | None = None
    scale: list[float] | None = None

    @classmethod
    def from_fitted(cls, transformer):
        numeric = transformer.named_transformers_["numericas"].named_steps
        imputer = numeric["simpleimputer"]
        scaler = numeric.get("standardscaler")
        return cls(
            list(transformer.transformers_[0][2]),
            imputer.statistics_.tolist(),
            imputer.indicator_.features_.tolist(),
            transformer.named_transformers_["estacion"].categories_[0].tolist(),
            transformer.get_feature_names_out().tolist(),
            scaler.mean_.tolist() if scaler is not None else None,
            scaler.scale_.tolist() if scaler is not None else None,
        )

    def transform(self, frame):
        values = frame[self.columns].to_numpy(dtype=float)
        missing = np.isnan(values)
        imputed = np.where(missing, self.medians, values)
        numeric = np.column_stack((imputed, missing[:, self.indicators]))
        if self.mean is not None:
            numeric = (numeric - self.mean) / self.scale
        stations = frame[STATION].to_numpy()[:, None] == self.stations
        return np.column_stack((numeric, stations))

    def get_feature_names_out(self):
        return np.asarray(self.feature_names)


def load_sources(data_dir, wind_path, downloads):
    air, weather, wind = load_hourly_sources(data_dir, wind_path, True, downloads)
    air = pd.concat([
        air[KEY + [CURRENT]], load_clean(data_dir / FILENAME, START, END),
        load_clean(data_dir / TEST_FILE, HOLDOUT_START, TEST_END),
    ], ignore_index=True)
    validate_hours(air, "lecturas de PM2.5 consolidadas")
    return air, weather, wind


def features(air, weather, wind, horizon, *, require_target=True):
    frame = build_matrix(air, weather, wind, horizon, require_target=require_target)
    return frame.merge(historical_features(air, horizon), on=KEY, validate="one_to_one")


def configure_tensorflow(jobs):
    import tensorflow as tf

    tf.config.threading.set_intra_op_parallelism_threads(jobs)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.experimental.enable_op_determinism()
    return tf


def train_model(tf, rows, calendar, grid, horizon, jobs, sample_weight=None):
    columns = GROUPS[GROUP_BY_HOUR[horizon]] + HISTORY
    if horizon == 1:
        candidate = next(spec for spec in candidates() if spec.name == "xgb_vecinos")
        candidate = replace(candidate, parameters=candidate.parameters | {"objective": "reg:absoluteerror"})
        model = make_regressor(candidate, jobs, columns)
        change = rows[TARGET] - rows[CURRENT]
        model.fit(rows[columns], change, xgbregressor__sample_weight=sample_weight)
        state = {"columnas": columns}
    else:
        model, state = fit_lstm(tf, rows, calendar, grid, horizon, sample_weight)
    return model, state | {"horizonte_h": horizon, "modelo": MODELS[horizon], "version": VERSION}


def predict(tf, bundle, rows, sequence=None):
    model, state = bundle
    if state["horizonte_h"] == 1:
        change = model.predict(rows[state["columnas"]])
        return np.maximum(0, rows[CURRENT].to_numpy() + change)
    return predict_lstm_sequence(tf, model, state, rows, sequence)


def save_bundle(directory, bundle):
    model, state = bundle
    path = Path(directory) / VERSION / str(state["horizonte_h"])
    path.mkdir(parents=True, exist_ok=True)
    if state["horizonte_h"] == 1:
        transform, regressor = (step for _, step in model.steps)
        regressor.save_model(path / "modelo.json")
    else:
        transform = state["transformador"]
        weights = [w.tolist() for w in model.get_weights()]
        (path / "pesos.json").write_text(json.dumps(weights, allow_nan=False) + "\n", encoding="utf-8")
    parameters = {key: value for key, value in state.items() if key != "transformador"}
    parameters["transformador"] = vars(HourlyTransform.from_fitted(transform))
    (path / "transformaciones.json").write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_bundle(tf, directory, horizon):
    path = Path(directory) / VERSION / str(horizon)
    state = json.loads((path / "transformaciones.json").read_text(encoding="utf-8"))
    if (state["horizonte_h"], state["modelo"], state["version"]) != (horizon, MODELS[horizon], VERSION):
        raise ValueError(f"El paquete de {horizon} h no corresponde a {VERSION}")
    transform = HourlyTransform(**state["transformador"])
    state["transformador"] = transform
    if horizon == 1:
        regressor = XGBRegressor()
        regressor.load_model(path / "modelo.json")
        model = make_pipeline(transform, regressor)
    else:
        model = lstm_network(tf, len(transform.feature_names))
        weights = json.loads((path / "pesos.json").read_text(encoding="utf-8"))
        model.set_weights([np.asarray(w, dtype=np.float32) for w in weights])
    return model, state
