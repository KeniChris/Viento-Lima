package com.vientolima.pronosticos.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.OffsetDateTime;

/** Una hora de la ventana de 24 horas. pm25 es null cuando la hora no tiene lectura. */
public record Observacion(
        @JsonProperty("fecha_hora") OffsetDateTime fechaHora,
        @JsonProperty("pm25") Double pm25) {
}
