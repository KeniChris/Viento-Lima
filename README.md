# Pronóstico horario de PM2.5 en Lima Metropolitana

El proyecto pronostica la concentración de PM2.5 en siete estaciones del SENAMHI. Los modelos estiman el cambio respecto a la medición de emisión. XGBoost pronostica a una hora y dos redes LSTM pronostican a tres y seis horas. La concentración se expresa en µg/m³. La alerta de propagación entre estaciones se evalúa con un clasificador de episodios.

## Web local

La web usa Angular para el dashboard, Spring Boot para la API, PostgreSQL para las cuentas y Redis para los pronósticos que publica Python. Para ejecutarla, usa Java 21, Maven, Node.js 24, Python 3.12 y Docker Compose.

1. Desde la raíz del repositorio, inicia PostgreSQL y Redis y calcula la demostración con `pm25-2`.

   ```bash
   docker compose -f codigo/docker-compose.yml up -d
   python -m pip install -r requirements.txt
   python -m codigo.publicar_pm25 demo
   ```

   Si ya tienes Redis en el puerto 6379, puedes iniciar solo `postgres` con Compose. La muestra incluida conserva las fechas originales de noviembre de 2024. El comando vuelve a cargar los pronósticos si reinicias Redis.

2. Desde la raíz del repositorio, abre una terminal para la API.

   ```bash
   cd codigo/backend
   mvn spring-boot:run
   ```

   Si tienes un `.env` de las primeras pruebas, usa los valores de [`codigo/backend/.env.example`](codigo/backend/.env.example). Con Compose, PostgreSQL escucha en el puerto local 15432.

3. Desde la raíz del repositorio, abre otra terminal para el dashboard.

   ```bash
   cd codigo/frontend
   npm ci
   npm start
   ```

4. Entra a [localhost:4200](http://localhost:4200), registra una cuenta local e inicia sesión. El dashboard abre la demostración histórica en San Borja. La medición fue **15,19 µg/m³** a las 08:00 del 15/11/2024 y `pm25-2` pronostica **18,53 µg/m³** para las 09:00. La gráfica conserva el hueco de las 02:00. La opción **Ver datos actuales** muestra el estado de las lecturas recientes cuando están disponibles en Redis.

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

El ajuste posterior de XGBoost a una hora se evaluó en 8 384 pares de enero–marzo de 2025. Con `pm25-2`, el MAE global pasó de 2,223 a 2,178 µg/m³ frente a `pm25-1`. Consulta la [rejilla, el desglose de la mejora y la evaluación por estación](resultados/ajuste_1h/).

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

### Ajuste de XGBoost a una hora

Con los insumos anteriores en `datos/`, ejecuta el ajuste antes de evaluar el trimestre reservado. Ambos comandos usan XGBoost y se ejecutan sin TensorFlow.

```bash
python -m codigo.ajustar_xgb ajustar
python -m codigo.preparar_lecturas_recientes --periodo ene-mar-2025
python -m codigo.ajustar_xgb evaluar
```

Define otra ruta con `--salida` si quieres comparar una reproducción con los resultados versionados. `--descargas-clima` admite las respuestas CSV de ERA5 e IFS por año (`era5_2024.csv`, `ifs_2024.csv`, `era5_2025.csv`, `ifs_2025.csv`). El candidato queda en `modelos/pm25-2/1/`.

## Pronóstico para la web local

Desde la raíz del repositorio, instala las dependencias con Python 3.12 y genera el pronóstico.

```bash
python -m pip install -r requirements.txt
python -m codigo.pronostico_local --salida pronostico.json
```

El comando ejecuta `pm25-2` y escribe un JSON con los campos del contrato del dashboard. La muestra incluida permite consultar las 07:00 y las 08:00 del 15 de noviembre de 2024 en la zona de Lima. Para reproducir la referencia se puede elegir `--version pm25-1`. Para consultar otras fechas, usa `--datos datos` y `--emision` con fecha, hora entera y desfase explícito. Usa `--descargas-clima ruta_a_descargas` si ya tienes guardadas las descargas meteorológicas.

- [Salida histórica de San Borja a las 07:00](demostracion/san_borja_2024-11-15_07h.json).
- Con una última medición de 13,35 µg/m³, `pm25-2` estima 16,09 µg/m³ para las 08:00. La lectura observada a las 08:00 fue 15,19 µg/m³ y queda fuera de la entrada a las 07:00.
- La respuesta indica `HISTORICO`, incluye 24 posiciones horarias y conserva el hueco de las 02:00 como `null`. Si la estación carece de lectura a la hora de emisión, devuelve `SIN_PRONOSTICO` con `pronostico: null`.

La demostración funciona con archivos locales. Sus [insumos](demostracion/insumos_2024-11-15.csv.gz) incluyen lecturas de SENAMHI vía OpenAQ y meteorología ERA5 e IFS del 8 al 15 de noviembre de 2024. La ventana empieza 168 horas antes de la primera emisión para calcular los rezagos. El módulo corta las entradas a la hora consultada y conserva la hora real de cálculo en `calculado_en`.

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

### Publicador de una hora para la web

El publicador carga el paquete entrenado, reconstruye los rezagos a partir de las observaciones de Redis y escribe en la clave de la estación, el horizonte, la emisión y la versión. Lee como máximo 168 horas anteriores a la emisión y la observación de emisión. Se puede ejecutar con Redis estándar. El siguiente ejemplo carga el tramo histórico real y reproduce el caso de San Borja previsto para la web.

```bash
python -m codigo.publicar_pm25 cargar-historico --version pm25-1 \
  --emision 2024-11-15T08:00:00-05:00
python -m codigo.publicar_pm25 publicar --modo historico --version pm25-1 \
  --estacion SAN_BORJA --emision 2024-11-15T08:00:00-05:00
```

Para emisiones actuales, configura `PM25_REDIS_URL` y `PM25_MODELO_VERSION=pm25-2`. Ejecuta `python -m codigo.publicar_pm25 publicar` después de que las observaciones estén disponibles en Redis en esa versión. El comando usa la hora entera actual en Lima y, en ausencia de `--estacion`, procesa las siete estaciones. Una emisión actual se publica antes de la hora objetivo y requiere una lectura válida en la hora de emisión. El modo histórico exige una hora objetivo transcurrida y registra `modo=HISTORICO`. Consulta el [contrato de claves](resultados/README.md#publicador-de-una-hora-para-la-web).

## Fuentes

- [SENAMHI, monitoreo horario de contaminantes](https://www.datosabiertos.gob.pe/dataset/monitoreo-de-los-contaminantes-del-aire-en-lima-metropolitana-servicio-nacional-de).
- [OpenAQ, archivo abierto en AWS](https://docs.openaq.org/aws/about). Mediciones de SENAMHI para Campo de Marte, San Borja, San Juan de Lurigancho y San Martín de Porres.
- [ERA5, meteorología horaria](https://doi.org/10.24381/cds.adbb2d47) y [Open-Meteo, archivo meteorológico](https://open-meteo.com/en/docs/historical-weather-api).
