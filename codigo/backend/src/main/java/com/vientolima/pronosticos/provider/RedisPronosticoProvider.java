package com.vientolima.pronosticos.provider;

import com.vientolima.config.RelojConfig;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

import java.time.OffsetDateTime;
import java.time.ZonedDateTime;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * LECTOR DE REDIS (listo para usar con {@code PM25_FUENTE=redis}).
 *
 * <p>Lee las claves que escribe {@code codigo/publicar_pm25.py} para la versión configurada:
 * <pre>
 *   pm25:observacion:&lt;version&gt;:&lt;ESTACION&gt;:&lt;segundos UTC&gt;       STRING JSON: PM2_5, fuente_pm25, fecha_unix…
 *   pm25:pronostico:&lt;version&gt;:&lt;ESTACION&gt;:&lt;segundos UTC&gt;:1h    HASH: emitido_en, objetivo_en, horizonte_h,
 *                                                                  modelo, version_modelo, pm25_estimado
 * </pre>
 * El instante de la clave es la hora de emisión (hora entera de Lima) en segundos UTC. Los campos sin medición
 * se guardan como null y se ignoran. El instante {@code calculado_en} puede faltar en datos antiguos.
 */
@Component
@ConditionalOnProperty(name = "app.pm25.fuente", havingValue = "redis")
public class RedisPronosticoProvider implements PronosticoProvider {
    private static final int HORAS_VENTANA = 24;

    private final StringRedisTemplate redis;
    private final JsonMapper json;
    private final String version;
    private final int horasBusqueda;

    public RedisPronosticoProvider(StringRedisTemplate redis, JsonMapper json,
                                   @Value("${app.pm25.version-modelo}") String version,
                                   @Value("${app.pm25.busqueda-ultima-medicion-horas:168}") int horasBusqueda) {
        this.redis = redis;
        this.json = json;
        this.version = version;
        this.horasBusqueda = Math.max(horasBusqueda, HORAS_VENTANA);
    }

    @Override
    public List<Medicion> mediciones(String estacion, OffsetDateTime desde, OffsetDateTime hasta) {
        List<ZonedDateTime> horas = new ArrayList<>();
        for (ZonedDateTime t = hora(desde); !t.isAfter(hora(hasta)); t = t.plusHours(1)) {
            horas.add(t);
        }
        return leer(estacion, horas);
    }

    @Override
    public Optional<Medicion> ultimaMedicion(String estacion, OffsetDateTime hasta) {
        List<ZonedDateTime> horas = new ArrayList<>();
        ZonedDateTime fin = hora(hasta);
        for (int i = 0; i < horasBusqueda; i++) {
            horas.add(fin.minusHours(i));
        }
        List<Medicion> lecturas = leer(estacion, horas);
        return lecturas.stream().max(java.util.Comparator.comparing(m -> m.fechaHora().toInstant()));
    }

    @Override
    public Optional<PronosticoDato> pronostico(String estacion, OffsetDateTime emision) {
        String clave = "pm25:pronostico:%s:%s:%d:1h".formatted(version, estacion, hora(emision).toEpochSecond());
        try {
            Map<Object, Object> campos = redis.opsForHash().entries(clave);
            if (campos == null || campos.isEmpty() || campos.get("pm25_estimado") == null) {
                return Optional.empty();
            }
            double estimado = Double.parseDouble(campos.get("pm25_estimado").toString());
            if (!Double.isFinite(estimado)) {
                return Optional.empty();
            }
            ZonedDateTime base = hora(emision);
            return Optional.of(new PronosticoDato(
                    base.toOffsetDateTime(),
                    fecha(campos.get("objetivo_en"), base.plusHours(1).toOffsetDateTime()),
                    fecha(campos.get("calculado_en"), null),
                    campos.get("horizonte_h") == null ? 1 : Integer.parseInt(campos.get("horizonte_h").toString()),
                    estimado,
                    campos.get("version_modelo") == null ? version : campos.get("version_modelo").toString()));
        } catch (RedisConnectionFailureException ex) {
            throw new ProveedorNoDisponibleException("Redis no responde", ex);
        } catch (NumberFormatException ex) {
            throw new ProveedorNoDisponibleException("Pronóstico con formato inesperado en " + clave, ex);
        }
    }

    /** Un solo MGET con las observaciones de las horas pedidas; las ausentes o sin PM2_5 se descartan. */
    private List<Medicion> leer(String estacion, List<ZonedDateTime> horas) {
        List<String> claves = horas.stream()
                .map(t -> "pm25:observacion:%s:%s:%d".formatted(version, estacion, t.toEpochSecond()))
                .toList();
        List<String> valores;
        try {
            valores = redis.opsForValue().multiGet(claves);
        } catch (RedisConnectionFailureException ex) {
            throw new ProveedorNoDisponibleException("Redis no responde", ex);
        }
        List<Medicion> lecturas = new ArrayList<>();
        if (valores == null) {
            return lecturas;
        }
        for (int i = 0; i < valores.size(); i++) {
            String crudo = valores.get(i);
            if (crudo == null) {
                continue;
            }
            Map<?, ?> obs = json.readValue(crudo, Map.class);
            Object pm25 = obs.get("PM2_5");
            if (pm25 instanceof Number n && Double.isFinite(n.doubleValue())) {
                Object fuente = obs.get("fuente_pm25");
                lecturas.add(new Medicion(horas.get(i).toOffsetDateTime(), n.doubleValue(),
                        fuente == null ? "SENAMHI" : fuente.toString()));
            }
        }
        return lecturas;
    }

    private static OffsetDateTime fecha(Object valor, OffsetDateTime porDefecto) {
        if (valor == null || valor.toString().isBlank()) {
            return porDefecto;
        }
        return OffsetDateTime.parse(valor.toString());
    }

    private static ZonedDateTime hora(OffsetDateTime t) {
        return t.atZoneSameInstant(RelojConfig.LIMA).truncatedTo(ChronoUnit.HOURS);
    }
}
