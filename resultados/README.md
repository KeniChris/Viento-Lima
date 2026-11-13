# Datos y resultados

| Ruta | Contenido |
| --- | --- |
| `pronostico/eda_openaq_oct_dic_2024.csv` | Cobertura, limpieza y distribución por estación de la prueba independiente. |
| `pronostico/validacion_pesos.csv`, `pronostico/pliegues_pesos.csv` | Efecto de ponderar episodios altos durante el ajuste. |
| `pronostico/modelos.json`, `pronostico/prueba.csv`, `pronostico/por_estacion.csv` | Selección, métricas globales y métricas por estación. |
| `pronostico/diferencia_persistencia.csv`, `pronostico/faltantes.csv` | Diferencias pareadas de MAE e intervalos por bloques de siete días. |
| `pronostico/predicciones.parquet`, `pronostico/figuras/` | Mediciones, pronósticos y figuras de los tres modelos. |
| `pronostico/redis.json`, `alertas/` | Verificación de Redis, matrices de confusión y umbrales de alza. |
| `historico/` | Comparaciones iniciales, selección previa, auditorías y datos de apoyo. |

## Pronóstico y verificación

Cada caso identifica `ESTACION`, `FECHA_HORA_LIMA` y `horizonte_h`. La fecha objetivo es la emisión más el horizonte. `PM2_5` es la concentración de emisión y `pm25_objetivo` es la medición futura usada en evaluación. `alta` indica que el objetivo alcanzó el P95 de desarrollo de la estación. El sesgo es pronosticado menos observado. Las concentraciones, el MAE, el RMSE y el sesgo se expresan en µg/m³. El MAE macro promedia los MAE de las estaciones con el mismo peso.

Los modelos reciben los rezagos exactos, medias recientes, cambios de concentración, calendario y datos meteorológicos disponibles en la emisión. El orden de variables se conserva en cada paquete. Las LSTM reciben 24 pasos horarios con dos canales, concentración estandarizada e indicador de presencia. Los faltantes de la secuencia tienen valor estandarizado cero e indicador cero. Las entradas de inferencia se construyen sin objetivo futuro.

## Redis

| Patrón de clave | Contenido |
| --- | --- |
| `pm25:observacion:pm25-1:<estación>:<segundos UTC>` | PM2.5, meteorología, viento y procedencia por estación y hora. Los campos sin medición se representan como null. |
| `pm25:entrada:pm25-1:<estación>:<segundos UTC>:<horizonte>h` | Variables de emisión y secuencia de 24 horas. |
| `pm25:pronostico:pm25-1:<estación>:<segundos UTC>:<horizonte>h` | Concentración estimada, horas de emisión y objetivo y versión del modelo. |
| `pm25:episodio:pm25-1:<estación>:<segundos UTC>` | Vector de seis variables estandarizadas, concentración de emisión y desenlace observado seis horas después. |
| `modelo:final:<horizonte>h` | Modelo seleccionado y métricas del periodo de prueba. |
| `pm25:metrica:pm25-1:*`, `pm25:alerta:pm25-1:*` | Métricas por estación y resultados del clasificador de episodios. |

El índice `idx:pm25:episodios:pm25-1` usa distancia L2 sobre PM2.5 actual, cambio en tres horas, media de seis horas, humedad y componentes este y norte del viento. La consulta filtra por estación y excluye episodios cuyo desenlace de seis horas no precede la emisión consultada. El desenlace se devuelve como contexto observado.
