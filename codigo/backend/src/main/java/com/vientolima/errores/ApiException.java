package com.vientolima.errores;

import org.springframework.http.HttpStatus;

import java.util.List;
import java.util.Map;

/** Error de negocio que el manejador global convierte en {@link ErrorApi} con el estado HTTP indicado. */
public class ApiException extends RuntimeException {
    private final HttpStatus estado;
    private final String codigo;
    private final Map<String, List<String>> campos;

    public ApiException(HttpStatus estado, String codigo, String mensaje) {
        this(estado, codigo, mensaje, null);
    }

    public ApiException(HttpStatus estado, String codigo, String mensaje, Map<String, List<String>> campos) {
        super(mensaje);
        this.estado = estado;
        this.codigo = codigo;
        this.campos = campos;
    }

    public static ApiException solicitudInvalida(String campo, String mensajeCampo) {
        return new ApiException(HttpStatus.BAD_REQUEST, "SOLICITUD_INVALIDA",
                "Revisa los campos de la solicitud.", Map.of(campo, List.of(mensajeCampo)));
    }

    public HttpStatus getEstado() {
        return estado;
    }

    public String getCodigo() {
        return codigo;
    }

    public Map<String, List<String>> getCampos() {
        return campos;
    }

    public ErrorApi aErrorApi() {
        return new ErrorApi(codigo, getMessage(), campos);
    }
}
