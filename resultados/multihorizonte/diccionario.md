# Diccionario de datos del pronóstico horario

Cada fila de entrada identifica una estación, una hora de emisión `t` y un horizonte `h`. Se conserva el calendario de Lima y se usa la hora exacta para los rezagos. Las concentraciones, el error y el sesgo se expresan en µg/m³.

| Variable | Tipo y unidad | Fuente y cálculo |
| --- | --- | --- |
| `ESTACION` | Categoría | Código del punto de monitoreo SENAMHI. |
| `FECHA_HORA_LIMA` | Fecha y hora local | Hora de emisión convertida desde UTC a `America/Lima`. |
| `FECHA_OBJETIVO`, `horizonte_h` | Fecha y hora local, horas | Hora de emisión más la anticipación. La selección usa 1, 3 y 6 horas. |
| `PM2_5`, `pm25_objetivo` | Decimal, µg/m³ | Medición de SENAMHI en `t` y en `t+h`. El objetivo queda fuera de las entradas. |
| `pm_lag_{1,3,6,24}h`, `pm_cambio_3h` | Decimal, µg/m³ | Medición previa exacta y diferencia `PM2.5(t) − PM2.5(t−3 h)`. |
| `pm_media_6h`, `pm_std_6h`, `pm_media_24h` | Decimal, µg/m³ | Resúmenes hasta `t`. Se requieren al menos cuatro mediciones en seis horas y doce en veinticuatro. |
| `hora_seno`, `hora_coseno`, `mes_seno`, `mes_coseno`, `fin_de_semana` | Decimal o booleano | Calendario de la hora objetivo, conocido en la emisión. |
| `temperature_2m`, `relative_humidity_2m`, `precipitation`, `pressure_msl` | °C, %, mm, hPa | ERA5 histórico en `t` por estación. |
| `ifs10_velocidad_ms`, `viento_este_ms`, `viento_norte_ms` | Decimal, m/s | IFS histórico a 10 m. Los componentes positivos apuntan al este y al norte. Calma por debajo de 0,5 m/s. |
| `vecinos_media_{0..6}h`, `vecinos_max_{0..6}h`, `vecinos_alza_{0..6}h` | Decimal, µg/m³ | Media ponderada por distancia, máximo y máximo aumento positivo en tres horas, entre las otras estaciones. |
| `transporte_pm_media`, `transporte_alza_media`, `transporte_alza_max` | Decimal, µg/m³ | Concentración y alzas vecinas con viento compatible con el rumbo y el tiempo de viaje. |
| `transporte_origenes`, `transporte_peso` | Conteo, peso relativo | Número de estaciones emisoras elegibles y suma de pesos. |
| `focos_50km_24h`, `focos_log_frp_50km_24h` | Conteo, log1p de MW | Detecciones VIIRS S-NPP de vegetación de confianza nominal o alta hasta `t−3 h`. Radio de 50 km y ventana de 24 h. |
| `focos_peso_llegada`, `focos_log_frp_llegada` | Peso relativo, log1p de MW ponderados | Detecciones y FRP ponderadas por rumbo y alcance a la hora objetivo. Viento histórico de la estación más cercana a cada detección. |
| `p95_pm25`, `p90_alza_origen` | Decimal, µg/m³ | Cortes de diagnóstico estimados con datos de desarrollo de cada horizonte. P95 es específico de la estación. |

## Resultados reproducibles

| Archivo | Contenido |
| --- | --- |
| `validacion.csv`, `pliegues.csv` | Selección y métricas de los tres pliegues por modelo y horizonte. |
| `prueba.csv`, `por_estacion.csv` | MAE, RMSE, R², episodios altos y cruces nuevos, con sus conteos. |
| `ablacion.csv` | Diferencia pareada de MAE y remuestreo descriptivo por semanas. Un único bloque deja el intervalo vacío. |
| `residuos.csv` | Medianas y percentiles de observado menos pronosticado, por rango del pronóstico. |
| `umbrales.csv` | Umbral P95 por estación y horizonte, y corte P90 de alzas de origen alineadas. |

La comparación histórica comprende enero de 2023 a abril de 2024. `openaq_2024` identifica la comprobación posterior de mayo y junio en cuatro estaciones. `alza_origen_alineada` indica un aumento máximo de tres horas en origen de magnitud al menos P90 de desarrollo. Los grupos de focos indican detecciones en la ventana de la estación o un peso de llegada positivo. Los picos se evalúan como cruces del P95 entre la emisión y la hora objetivo. La categoría oficial del INCA de PM2.5 utiliza el promedio móvil de 24 horas.
