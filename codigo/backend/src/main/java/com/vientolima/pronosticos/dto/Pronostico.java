package com.vientolima.pronosticos.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.OffsetDateTime;

public record Pronostico(
        @JsonProperty("emitido_en") OffsetDateTime emitidoEn,
        @JsonProperty("objetivo_en") OffsetDateTime objetivoEn,
        @JsonProperty("calculado_en") OffsetDateTime calculadoEn,
        @JsonProperty("horizonte_h") int horizonteH,
        @JsonProperty("pm25_estimado") double pm25Estimado,
        @JsonProperty("version_modelo") String versionModelo) {
}
