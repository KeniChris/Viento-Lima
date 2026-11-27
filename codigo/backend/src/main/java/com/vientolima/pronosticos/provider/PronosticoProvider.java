package com.vientolima.pronosticos.provider;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * PUNTO DE CONEXIÓN con la fuente de pronósticos.
 *
 * <p>El servicio de consulta solo conoce esta interfaz. Hay dos implementaciones, elegidas con
 * {@code app.pm25.fuente} (variable PM25_FUENTE):
 * <ul>
 *   <li>{@code fixtures}: {@link FixturePronosticoProvider}, datos de prueba del contrato OpenAPI.</li>
 *   <li>{@code redis}: {@link RedisPronosticoProvider}, lector de las claves que escribe
 *       {@code codigo/cargar_redis.py}.</li>
 * </ul>
 * Para conectar otra fuente (por ejemplo PostgreSQL) crea otra clase {@code @Component} que implemente esta
 * interfaz con {@code @ConditionalOnProperty(name = "app.pm25.fuente", havingValue = "<nombre>")} y define
 * ese valor en PM25_FUENTE. No hay que tocar controladores ni servicio.
 *
 * <p>Todas las fechas son instantes; el servicio las presenta en America/Lima (-05:00).
 * Si la fuente falla, lanzar {@link ProveedorNoDisponibleException}.
 */
public interface PronosticoProvider {

    /** Lecturas con PM2.5 entre {@code desde} y {@code hasta}, ambos inclusive, en horas enteras. */
    List<Medicion> mediciones(String estacion, OffsetDateTime desde, OffsetDateTime hasta);

    /** Última lectura con PM2.5 en o antes de {@code hasta}, aunque sea anterior a la ventana de 24 horas. */
    Optional<Medicion> ultimaMedicion(String estacion, OffsetDateTime hasta);

    /** Pronóstico a 1 h emitido con la medición de {@code emision} (hora entera de Lima). */
    Optional<PronosticoDato> pronostico(String estacion, OffsetDateTime emision);
}
