# PM2.5 en Lima Metropolitana

El Hito 3 pronostica la concentración de PM2.5 a una hora en siete estaciones del SENAMHI. Las horas del CSV original están en **UTC** y el PM2.5 se expresa en **µg/m³**. El cruce con OpenAQ verificó 112 de 112 pares de cuatro estaciones a la precisión publicada. Las fechas se convierten a `America/Lima` antes de cruzar la meteorología. El notebook del Hito 2 contiene el flujo previo con la conversión corregida.

## Ejecutar el Hito 3

Clona la rama `develop` y ejecuta los comandos desde la carpeta que contiene `codigo/` y `Hito3_PM25_Lima.ipynb`. El experimento publicado se ejecutó con Python 3.12. El notebook abre las tablas verificadas con `RECALCULAR = False`. En Colab puede clonar la rama al arrancar. Activa el recálculo para reconstruir los resultados desde los insumos.

Descarga estos archivos de la carpeta de insumos del equipo enlazada en el informe. Crea `datos/` en la raíz de `Viento-Lima` y copia allí los archivos con estos nombres exactos. Puedes colocar el ZIP directamente en `datos/`.

- `aire_lima_completo.csv`
- `clima_era5_7_estaciones.parquet`
- `poblacion_censo2017_7_distritos.csv`
- `csv_complementarios_pm25_lima.zip`

### Windows con PowerShell

```powershell
python -m venv .venv
$pythonHito3 = ".\.venv\Scripts\python.exe"
& $pythonHito3 -m pip install -r requirements.txt
& $pythonHito3 -m codigo.actualizar_aire
& $pythonHito3 -m codigo.viento
& $pythonHito3 -m codigo.preparar
& $pythonHito3 -m codigo.evaluar --hilos 4
```

El entorno virtual aísla las dependencias del proyecto de las del Python global. Si los insumos están en otra carpeta, añade `--datos "ruta de los insumos"` a `codigo.actualizar_aire` y `codigo.preparar`. El archivo de viento se busca en `datos/` por defecto. Puedes indicar otra ubicación con `--viento`.

### Linux y macOS

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m codigo.actualizar_aire
python -m codigo.viento
python -m codigo.preparar
python -m codigo.evaluar --hilos 4
```

`codigo.evaluar` necesita `resultados/matriz_hito3.parquet`. Ejecuta primero `codigo.preparar` y comprueba que terminó antes de entrenar.

`codigo.viento` descarga y limpia 449.736 registros IFS a 10 m desde enero de 2017 hasta abril de 2024. Puedes pasar CSV ya descargados con `--descargas`. `codigo.preparar` escribe `resultados/aire_senamhi_normalizado.parquet` con las horas UTC y Lima, las mediciones originales y sus faltantes. La matriz del modelo se genera a partir de esas horas corregidas.

## Resultados principales

| Modelo | MAE macro en validación | MAE global en prueba | RMSE en prueba |
| --- | ---: | ---: | ---: |
| XGBoost base | 4,138 | 2,882 | 4,980 |
| Random Forest con transporte | 4,048 | 2,896 | 4,833 |
| HistGradientBoosting con transporte | 4,096 | 2,930 | 5,023 |
| Persistencia | 4,376 | 3,196 | 5,378 |

La prueba histórica contiene 56.035 pronósticos. XGBoost base tuvo el menor MAE y Random Forest el menor RMSE. La ablación de XGBoost dio un cambio de MAE de +0,0156 µg/m³ al añadir transporte al grupo con PM2.5 vecino y viento local. El intervalo descriptivo del 95 % por bloques semanales fue de +0,0064 a +0,0267 µg/m³.

## Prueba de mayo y junio de 2024

OpenAQ aportó 4.435 lecturas nuevas de cuatro estaciones del SENAMHI. Con los rezagos y el objetivo a una hora quedaron 3.587 casos comparables posteriores al corte original. Se usaron modelos ajustados con datos anteriores a 2023. El MAE fue 4,691 µg/m³ para Random Forest, 4,699 para XGBoost base y 5,093 para persistencia. El cambio de MAE por añadir transporte a XGBoost fue −0,0013 µg/m³, con un intervalo descriptivo del 95 % de −0,0181 a +0,0159.

```bash
python -m codigo.actualizar_aire --extender
python -m codigo.validar_extension --hilos 4
```

`acceso_openaq_2024.csv` registra los archivos diarios obtenidos y los que devolvieron 404 o 503. `cobertura_openaq_2024.csv` y `soporte_extension_2024.csv` muestran el alcance por estación. La extensión aporta una prueba posterior parcial con el mismo horizonte.

## Diseño del experimento

| Grupo | Entradas |
| --- | --- |
| Base | Los 54 predictores del Hito 2 y su configuración de XGBoost |
| Viento | Base más velocidad y componentes IFS a 10 m, calma y disponibilidad |
| Vecinos | Viento más PM2.5 de otras estaciones a las horas t, t−1, t−3 y t−5, resumido con media por distancia, máximo y número de observaciones |
| Transporte | Vecinos más cinco predictores ponderados por rumbo, velocidad proyectada, distancia y rezago |

La comparación con hiperparámetros fijos controla el efecto de agregar cada grupo. Además se ajustan variantes de XGBoost, HistGradientBoosting y Random Forest. La selección usa el promedio de MAE por estación en los pliegues expansivos de 2020, 2021 y 2022. Cada pliegue entrena con años previos y purga los objetivos que cruzan el corte. La prueba histórica de 2023 a abril de 2024 se usa para comparar con el Hito 2. Las etiquetas futuras y el PM2.5 sin medición quedan fuera de los predictores. Los imputadores meteorológicos se ajustan dentro de cada entrenamiento.

La verificación comparó ambos relojes para las mismas concentraciones. Los 112 pares coinciden bajo UTC con diferencia máxima de 0,005 µg/m³ por redondeo. Como hora de Lima coincidieron 0 de 112. En San Borja, por ejemplo, 10,19 µg/m³ a las 06:00 del CSV el 15 de abril de 2024 coincide con 01:00−05:00 en OpenAQ. El calendario original convertido abarca del 1 de enero de 2015 al 30 de abril de 2024 inclusive.

La dirección meteorológica indica la procedencia. Se convierte en un vector de desplazamiento y se proyecta sobre el rumbo de la estación de origen a la receptora. El peso favorece una diferencia angular de hasta 45° y un tiempo de viaje compatible con la distancia. Para un pronóstico en t+1 se revisa PM2.5 en t a t−5 y viento histórico en esas mismas horas. Con velocidades menores a 0,5 m/s se registra calma y se descarta el ángulo para el transporte. Es una aproximación predictiva de la advección entre estaciones. La resolución de IFS es de aproximadamente 9 km y la evaluación usa datos meteorológicos de archivo.

| Archivo de resultados | Evidencia |
| --- | --- |
| `auditoria_preparacion.csv`, `cobertura_viento.csv` | Limpieza y disponibilidad |
| `distancias_y_rumbos.csv` | Geometría de los pares origen–destino |
| `cv_modelos.csv`, `cv_por_pliegue.csv`, `cv_soporte_estaciones.csv` | Validación y ajuste |
| `prueba_modelos.csv`, `prueba_por_estacion.csv`, `umbrales_altos.csv` | Error, cobertura y episodios P95 |
| `diferencias_prueba.csv`, `prueba_por_alineacion.csv` | Ablación pareada y análisis por condiciones |
| `permutacion_validacion_2022.csv`, `resumen.json` | Dependencia predictiva y modelo elegido |
| `verificacion_horaria.csv`, `ejemplos_hora_verificada.csv` | Pares UTC y hora de Lima, unidad y fuentes |
| `conexion_postgresql.json`, `conexion_pronosticos.csv` | Lectura y escritura con PostgreSQL local |

## Integración con PostgreSQL

La prueba local siembra filas históricas preparadas en `pm25_hito3.entradas`, las lee con Psycopg, aplica el pipeline entrenado y hace upsert en `pm25_hito3.pronosticos`. El contrato está en `codigo/contrato_postgresql.sql`. El módulo de datos que alimente la base debe proporcionar una entrada por estación y hora de emisión, con el JSON de predictores definido por el modelo. Las horas se almacenan como `TIMESTAMPTZ`.

```bash
python -m pip install "psycopg[binary]>=3.3,<4" "pgserver==0.1.4"
python -m codigo.bd --demo-local
```

La demostración levanta PostgreSQL temporal por loopback en Linux y comprueba el mismo resultado al repetir la carga de pronósticos. Para una base compartida ya provisionada y con entradas cargadas, configura la variable de entorno `PM25_DATABASE_URL` y ejecuta `python -m codigo.bd --emitido-en 2023-01-01T12:00:00-05:00` con una hora disponible. La conexión con el módulo de IA del curso requiere su endpoint y el esquema acordado.

## Fuentes

- [SENAMHI, Monitoreo de los contaminantes del aire en Lima Metropolitana](https://www.datosabiertos.gob.pe/dataset/monitoreo-de-los-contaminantes-del-aire-en-lima-metropolitana-servicio-nacional-de).
- [OpenAQ Open Data on AWS](https://docs.openaq.org/aws/about), observaciones de SENAMHI con desfase horario y unidades explícitas. Ubicaciones [Campo de Marte](https://explore.openaq.org/locations/2415), [San Borja](https://explore.openaq.org/locations/2402), [San Juan de Lurigancho](https://explore.openaq.org/locations/7464) y [San Martín de Porres](https://explore.openaq.org/locations/2351).
- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api), datos ECMWF IFS de velocidad y dirección a 10 m. Atribución [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- [ECMWF, convención de dirección meteorológica y componentes del viento](https://confluence.ecmwf.int/pages/viewpage.action?pageId=242074496).
- [Datos hidrometeorológicos de SENAMHI](https://www.datosabiertos.gob.pe/dataset/datos-hidrometeorol%C3%B3gicos-de-libre-acceso), fuente identificada para contrastar el viento modelado con mediciones disponibles por estación.

Para reproducir el Colab del Hito 2 también se requiere `poblacion_proyectada_2018_2026_7_distritos.csv`. Extrae los CSV de `csv_complementarios_pm25_lima.zip` directamente bajo `datos_complementarios_pm25/` dentro de la carpeta indicada por `PM25_DATA_DIR`. Esa estructura permite que el notebook original localice los archivos del ZIP, que viene con una carpeta interna.
