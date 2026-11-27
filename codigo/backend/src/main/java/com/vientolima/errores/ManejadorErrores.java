package com.vientolima.errores;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestControllerAdvice
public class ManejadorErrores {

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ErrorApi> manejarApi(ApiException ex) {
        return ResponseEntity.status(ex.getEstado()).body(ex.aErrorApi());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorApi> manejarValidacion(MethodArgumentNotValidException ex) {
        Map<String, List<String>> campos = new LinkedHashMap<>();
        ex.getBindingResult().getFieldErrors().forEach(error ->
                campos.computeIfAbsent(error.getField(), k -> new java.util.ArrayList<>())
                        .add(error.getDefaultMessage()));
        return solicitudInvalida(campos);
    }

    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<ErrorApi> manejarParametroFaltante(MissingServletRequestParameterException ex) {
        return solicitudInvalida(Map.of(ex.getParameterName(), List.of("Este parámetro es obligatorio.")));
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentTypeMismatchException.class})
    public ResponseEntity<ErrorApi> manejarFormato(Exception ex) {
        return solicitudInvalida(null);
    }

    private ResponseEntity<ErrorApi> solicitudInvalida(Map<String, List<String>> campos) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(new ErrorApi("SOLICITUD_INVALIDA", "Revisa los campos de la solicitud.", campos));
    }
}
