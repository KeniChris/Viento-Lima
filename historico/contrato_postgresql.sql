CREATE SCHEMA IF NOT EXISTS pm25_hito3;

CREATE TABLE IF NOT EXISTS pm25_hito3.entradas (
    estacion TEXT NOT NULL,
    emitido_en TIMESTAMPTZ NOT NULL,
    predictores JSONB NOT NULL CHECK (jsonb_typeof(predictores) = 'object'),
    fuente TEXT NOT NULL,
    PRIMARY KEY (estacion, emitido_en)
);

CREATE TABLE IF NOT EXISTS pm25_hito3.pronosticos (
    estacion TEXT NOT NULL,
    emitido_en TIMESTAMPTZ NOT NULL,
    objetivo_en TIMESTAMPTZ NOT NULL CHECK (objetivo_en = emitido_en + INTERVAL '1 hour'),
    version_modelo TEXT NOT NULL,
    pm25_estimado DOUBLE PRECISION NOT NULL CHECK (pm25_estimado >= 0 AND pm25_estimado < 'Infinity'),
    registrado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (estacion, emitido_en, version_modelo),
    FOREIGN KEY (estacion, emitido_en) REFERENCES pm25_hito3.entradas (estacion, emitido_en)
);
