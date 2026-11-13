# Comparaciones iniciales

[Cuaderno de modelado inicial](modelado_inicial.ipynb) y [resultados de las comparaciones](../resultados/historico/).

Los experimentos de una hora se reproducen con `codigo.preparar` y `codigo.evaluar`. Requieren, además de los insumos principales, `poblacion_censo2017_7_distritos.csv` y `csv_complementarios_pm25_lima.zip`. El cuaderno inicial usa también `poblacion_proyectada_2018_2026_7_distritos.csv` y, si se descomprime el ZIP, busca sus CSV en `datos_complementarios_pm25/` dentro de la carpeta de insumos.

`codigo.pronosticar` reproduce la comparación multihorizonte. `codigo.analizar_mae` compara rezagos y capa límite, y `codigo.evaluar_lstm` recupera su tabla de validación desde `resultados/historico/analisis_mae/`. La prueba de PostgreSQL pertenece a la comparación inicial de una hora y se ejecuta con `python -m historico.bd_postgresql --demo-local` tras generar su modelo con `codigo.evaluar`.
