package com.vientolima.pronosticos.provider;

import java.time.OffsetDateTime;

/**
 * Pronóstico a una hora tal como lo entrega la fuente.
 *
 * @param emitidoEn   hora entera de Lima de la medición usada como base
 * @param objetivoEn  emitidoEn + 1 h
 * @param calculadoEn instante real en que se ejecutó el modelo; null si la fuente no lo guarda
 */
public record PronosticoDato(OffsetDateTime emitidoEn, OffsetDateTime objetivoEn, OffsetDateTime calculadoEn,
                             int horizonteH, double pm25Estimado, String versionModelo) {
}
