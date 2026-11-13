# Pronóstico horario de PM2.5 en Lima Metropolitana

El proyecto pronostica la concentración de PM2.5 en siete estaciones del SENAMHI. Los modelos estiman el cambio respecto a la medición de emisión. XGBoost pronostica a una hora y dos redes LSTM pronostican a tres y seis horas. La concentración se expresa en µg/m³. La alerta de propagación entre estaciones se evalúa con un clasificador de episodios.

## Resultados

La selección empleó los pliegues cronológicos de 2020, 2021 y 2022. Julio-septiembre de 2024 se usó como comprobación de la selección. La prueba independiente abarcó octubre-diciembre de 2024 en cuatro estaciones.

| Horizonte | Modelo | Pares de prueba | MAE de persistencia | MAE del modelo | MAE en episodios altos |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 h | XGBoost con pérdida absoluta y peso 3 en episodios altos | 7.969 | 2,810 | 2,471 | 8,078 |
| 3 h | LSTM directa de 24 horas | 7.940 | 5,488 | 4,335 | 18,894 |
| 6 h | LSTM directa de 24 horas | 7.920 | 7,260 | 5,027 | 22,850 |

La persistencia repite la concentración medida al emitir. Los episodios altos se definen mediante el percentil 95 de desarrollo por estación. Al simular un corte de seis horas previas, el MAE pareado a seis horas subió de 5,020 a 8,139 µg/m³. La evaluación usa meteorología de archivo y emisiones históricas.

- [Pronóstico, auditoría y figuras](resultados/pronostico/)
- [Matrices de confusión y umbrales](resultados/alertas/)
- [Comparaciones previas](resultados/historico/)
- [Diccionario de datos y esquema de Redis](resultados/README.md)
- [Cuaderno de evaluación](Hito3_PM25_Lima.ipynb)

## Entorno e insumos

Los modelos se entrenaron con Python 3.12, XGBoost 3.2 y TensorFlow 2.21. Los archivos JSON en `modelos/pm25-1/` contienen el ensamble ajustado, los pesos de las redes, el orden de variables, la imputación y el escalado.

```bash
python -m pip install -r requirements.txt
python -m pip install "tensorflow-cpu==2.21.0" "keras==3.15.1"
```

Coloca `aire_lima_completo.csv` y `clima_era5_7_estaciones.parquet` en `datos/`. Los archivos originales se identifican en el anexo de fuentes del proyecto. El CSV del SENAMHI está en UTC y se convierte a `America/Lima` antes del cruce horario. Los scripts obtienen las extensiones de OpenAQ y el viento a 10 m de IFS.

```bash
python -m codigo.viento
python -m codigo.actualizar_aire --extender
python -m codigo.preparar_lecturas_recientes
python -m codigo.preparar_lecturas_recientes --periodo oct-dic
```

## Evaluación reproducible

```bash
python -m codigo.evaluar_final
python -m codigo.evaluar_faltantes
python -m codigo.eventos_viento --prueba-final
python -m codigo.visualizar
```

Para reproducir las comparaciones iniciales se requieren también los insumos complementarios de población y meteorología, y el ZIP de datos complementarios descrito en [el archivo histórico](historico/README.md).

## Redis

El almacenamiento usa Redis 8 con Redis Search. Las observaciones se guardan por estación y hora, las predicciones por estación, hora, horizonte y versión, y las ventanas históricas se indexan para recuperar sus vecinos más cercanos. El módulo de preguntas puede consultar las claves de métricas o recuperar episodios medidos similares a una emisión.

```bash
docker run --name pm25-redis -p 127.0.0.1:6379:6379 -d redis:8.4.8
python -m codigo.cargar_redis cargar
python -m codigo.cargar_redis pronosticar
python -m codigo.cargar_redis similares
python -m codigo.cargar_redis verificar
```

Los comandos de consulta aceptan `--estacion` y `--emision`. El ejemplo usa San Borja y el 15 de noviembre de 2024 a las 08:00 de Lima. La URL se configura con `PM25_REDIS_URL`. La verificación contrasta los tres pronósticos con `predicciones.parquet` y los cinco vecinos con una búsqueda exacta.

## Fuentes

- [SENAMHI, monitoreo horario de contaminantes](https://www.datosabiertos.gob.pe/dataset/monitoreo-de-los-contaminantes-del-aire-en-lima-metropolitana-servicio-nacional-de).
- [OpenAQ, archivo abierto en AWS](https://docs.openaq.org/aws/about). Mediciones de SENAMHI para Campo de Marte, San Borja, San Juan de Lurigancho y San Martín de Porres.
- [ERA5, meteorología horaria](https://doi.org/10.24381/cds.adbb2d47) y [Open-Meteo, archivo meteorológico](https://open-meteo.com/en/docs/historical-weather-api).
