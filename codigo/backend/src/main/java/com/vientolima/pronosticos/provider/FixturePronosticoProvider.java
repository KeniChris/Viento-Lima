package com.vientolima.pronosticos.provider;

import com.vientolima.config.RelojConfig;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Datos de prueba basados en los ejemplos del contrato OpenAPI (San Borja, 15/11/2024).
 *
 * <p>El perfil de PM2.5 por hora del día es el de la ventana 14/11 09:00 – 15/11 08:00 del contrato, así que esa
 * consulta devuelve exactamente los valores del ejemplo (15,19 a las 08:00 y 17,82 a las 09:00). Las demás
 * estaciones escalan ese perfil. La hora 02:00 no tiene lectura (hueco del gráfico). Casos para probar estados:
 * <ul>
 *   <li>CAMPO_DE_MARTE: tiene lecturas pero no pronóstico → SIN_PRONOSTICO.</li>
 *   <li>CARABAYLLO: no tiene lecturas → SIN_DATOS_RECIENTES.</li>
 * </ul>
 */
@Component
@ConditionalOnProperty(name = "app.pm25.fuente", havingValue = "fixtures", matchIfMissing = true)
public class FixturePronosticoProvider implements PronosticoProvider {
    private static final String FUENTE = "SENAMHI vía OpenAQ";
    private static final String VERSION = "pm25-1";
    /** Pronóstico del contrato para San Borja a las 08:00. */
    private static final double ESTIMADO_CONTRATO = 17.81681746482849;
    private static final double MEDICION_CONTRATO = 15.19;

    /** PM2.5 por hora del día (índice 0 = 00:00). null = sin lectura. */
    private static final Double[] PERFIL = {
            16.25, 15.54, null, 12.58, 12.19, 11.74, 12.03, 13.35, 15.19,
            24.74, 27.61, 21.95, 26.43, 18.97, 16.38, 9.77, 10.70, 10.59, 10.19,
            12.13, 14.69, 16.09, 16.96, 18.03};

    private static final Map<String, Double> FACTOR = Map.of(
            "SAN_BORJA", 1.0,
            "CAMPO_DE_MARTE", 1.1,
            "SAN_JUAN_DE_LURIGANCHO", 1.15,
            "SAN_MARTIN_DE_PORRES", 0.9,
            "SANTA_ANITA", 1.05,
            "VILLA_MARIA_DEL_TRIUNFO", 1.2);

    private final Clock reloj;

    public FixturePronosticoProvider(Clock reloj) {
        this.reloj = reloj;
    }

    @Override
    public List<Medicion> mediciones(String estacion, OffsetDateTime desde, OffsetDateTime hasta) {
        List<Medicion> lecturas = new ArrayList<>();
        ZonedDateTime t = hora(desde);
        ZonedDateTime fin = hora(hasta);
        while (!t.isAfter(fin)) {
            Optional<Double> v = valor(estacion, t);
            if (v.isPresent()) {
                lecturas.add(new Medicion(t.toOffsetDateTime(), v.get(), FUENTE));
            }
            t = t.plusHours(1);
        }
        return lecturas;
    }

    @Override
    public Optional<Medicion> ultimaMedicion(String estacion, OffsetDateTime hasta) {
        ZonedDateTime t = hora(hasta);
        for (int i = 0; i < 48; i++, t = t.minusHours(1)) {
            Optional<Double> v = valor(estacion, t);
            if (v.isPresent()) {
                return Optional.of(new Medicion(t.toOffsetDateTime(), v.get(), FUENTE));
            }
        }
        return Optional.empty();
    }

    @Override
    public Optional<PronosticoDato> pronostico(String estacion, OffsetDateTime emision) {
        if (estacion.equals("CAMPO_DE_MARTE")) {
            return Optional.empty();
        }
        ZonedDateTime base = hora(emision);
        Optional<Double> medicion = valor(estacion, base);
        if (medicion.isEmpty()) {
            return Optional.empty();
        }
        double estimado = medicion.get() == MEDICION_CONTRATO && estacion.equals("SAN_BORJA")
                ? ESTIMADO_CONTRATO
                : medicion.get() * (ESTIMADO_CONTRATO / MEDICION_CONTRATO);
        // El modelo "corrió" 12 minutos después de la hora de emisión, sin pasar del reloj actual.
        OffsetDateTime calculado = base.toOffsetDateTime().plus(Duration.ofMinutes(12));
        OffsetDateTime ahora = OffsetDateTime.now(reloj).withOffsetSameInstant(ZoneOffset.ofHours(-5));
        if (calculado.isAfter(ahora)) {
            calculado = ahora;
        }
        return Optional.of(new PronosticoDato(base.toOffsetDateTime(), base.plusHours(1).toOffsetDateTime(),
                calculado, 1, estimado, VERSION));
    }

    private static Optional<Double> valor(String estacion, ZonedDateTime hora) {
        Double factor = FACTOR.get(estacion);
        if (factor == null) {
            return Optional.empty();
        }
        Double base = PERFIL[hora.getHour()];
        if (base == null) {
            return Optional.empty();
        }
        return Optional.of(Math.round(base * factor * 100.0) / 100.0);
    }

    private static ZonedDateTime hora(OffsetDateTime t) {
        return t.atZoneSameInstant(RelojConfig.LIMA).truncatedTo(ChronoUnit.HOURS);
    }
}
