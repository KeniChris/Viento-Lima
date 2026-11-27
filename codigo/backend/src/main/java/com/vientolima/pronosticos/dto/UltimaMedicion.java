package com.vientolima.pronosticos.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.OffsetDateTime;

public record UltimaMedicion(
        @JsonProperty("fecha_hora") OffsetDateTime fechaHora,
        @JsonProperty("pm25") double pm25,
        @JsonProperty("fuente") String fuente) {
}
