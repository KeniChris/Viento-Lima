# Ajuste y prueba de XGBoost a una hora

La selección usó validación expansiva en 2020, 2021 y 2022. El percentil 95 de cada estación y la imputación se calcularon dentro de cada entrenamiento. Se eligió profundidad 5, tasa 0,03, 300 árboles y peso 3 para los episodios altos. El MAE macro en validación pasó de 3,891 a 3,861 µg/m³ y el de episodios altos pasó de 8,379 a 8,290 µg/m³.

La prueba reservada de enero a marzo de 2025 compara las mismas 8 384 horas objetivo de cuatro estaciones. Los 89 episodios altos usan el P95 por estación del entrenamiento 2017–2022. La validación y la prueba usan meteorología de archivo.

| Modelo | MAE global | MAE macro | MAE en altos | Sesgo en altos |
| --- | ---: | ---: | ---: | ---: |
| Persistencia | 2,559 | 2,558 | 15,562 | −3,714 |
| A · pm25-1 | 2,223 | 2,223 | 14,785 | −6,103 |
| B · pm25-1 con más datos | 2,219 | 2,218 | 14,643 | −5,760 |
| C · pm25-2 | 2,178 | 2,177 | 14,371 | −6,848 |

Las métricas tienen unidad µg/m³. El sesgo es estimado menos observado. A entrena con 207 756 pares hasta 2022. B y C comparten 278 045 pares hasta septiembre de 2024. El cambio de MAE global de B frente a A fue −0,0047, con IC del 95 % de −0,0105 a 0,0004. C frente a B cambió −0,0409, con intervalo de −0,0635 a −0,0225. Los intervalos se calcularon por bloques de siete días. C redujo ambos errores de selección frente a A, con mayor subestimación media en los episodios altos. Detectó cuatro de 35 cruces al P95 en la prueba de 2025.

| Archivo | Contenido |
| --- | --- |
| [seleccion.json](seleccion.json) | Hiperparámetros fijados con los pliegues de desarrollo. |
| [validacion.csv](validacion.csv), [pliegues.csv](pliegues.csv), [cv_por_estacion.csv](cv_por_estacion.csv) | Profundidad, rejilla de tasa y árboles, ponderaciones y métricas por pliegue y por estación. |
| [rejilla.png](rejilla.png) | Efecto combinado de la tasa y el número de árboles. El recuadro marca la combinación seleccionada. |
| [prueba.csv](prueba.csv), [por_estacion.csv](por_estacion.csv), [diferencias.csv](diferencias.csv) | Evaluación de octubre–diciembre de 2024 y enero–marzo de 2025, con diferencias pareadas e intervalos. |
| [decision.json](decision.json), [umbrales_altos.csv](umbrales_altos.csv) | Versión recomendada, cortes, reproducción de la base y P95 de evaluación. |
| [eda_openaq_ene_mar_2025.csv](eda_openaq_ene_mar_2025.csv), [acceso_openaq_ene_mar_2025.csv](acceso_openaq_ene_mar_2025.csv) | Cobertura de las lecturas y disponibilidad de los archivos diarios. |
| [redis.json](redis.json) | Reproducción histórica y comprobaciones del publicador de una hora. |

El paquete de inferencia está en [`modelos/pm25-2/1/`](../../modelos/pm25-2/1/). La prueba de San Borja conserva `pm25-1` como versión de demostración para comparar con el valor de referencia del contrato web.
