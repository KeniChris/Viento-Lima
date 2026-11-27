package com.vientolima.pronosticos.provider;

import java.time.OffsetDateTime;

/** Lectura horaria de PM2.5 (µg/m³) con su procedencia. Solo existen las horas que tienen lectura. */
public record Medicion(OffsetDateTime fechaHora, double pm25, String fuente) {
}
