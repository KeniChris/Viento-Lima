package com.vientolima.errores;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;
import java.util.Map;

/** Formato común de errores del contrato: codigo, mensaje y, si corresponde, campos. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ErrorApi(String codigo, String mensaje, Map<String, List<String>> campos) {

    public ErrorApi(String codigo, String mensaje) {
        this(codigo, mensaje, null);
    }
}
