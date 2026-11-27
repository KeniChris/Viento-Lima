package com.vientolima.pronosticos.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/** Resumen completo de una estación para un único corte de tiempo (esquema ConsultaPronostico del contrato). */
public record ConsultaPronostico(
        @JsonProperty("estacion") String estacion,
        @JsonProperty("zona_horaria") String zonaHoraria,
        @JsonProperty("unidad") String unidad,
        @JsonProperty("modo") String modo,
        @JsonProperty("estado") String estado,
        @JsonProperty("ultima_medicion") UltimaMedicion ultimaMedicion,
        @JsonProperty("pronostico") Pronostico pronostico,
        @JsonProperty("observaciones") List<Observacion> observaciones) {
}
