package com.vientolima.pronosticos.provider;

/** La fuente (Redis, base de datos…) falló temporalmente. El servicio la traduce a 503 FUENTE_NO_DISPONIBLE. */
public class ProveedorNoDisponibleException extends RuntimeException {
    public ProveedorNoDisponibleException(String mensaje, Throwable causa) {
        super(mensaje, causa);
    }
}
