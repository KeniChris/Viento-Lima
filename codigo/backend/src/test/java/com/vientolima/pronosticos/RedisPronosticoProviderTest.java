package com.vientolima.pronosticos;

import com.vientolima.pronosticos.provider.Medicion;
import com.vientolima.pronosticos.provider.PronosticoDato;
import com.vientolima.pronosticos.provider.ProveedorNoDisponibleException;
import com.vientolima.pronosticos.provider.RedisPronosticoProvider;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.HashOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import tools.jackson.databind.json.JsonMapper;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Verifica el formato de claves y valores que escribe codigo/cargar_redis.py, sin necesitar un Redis real. */
class RedisPronosticoProviderTest {
    private static final OffsetDateTime EMISION = OffsetDateTime.parse("2024-11-15T08:00:00-05:00");
    private static final long EPOCH_EMISION = 1731675600L;

    private StringRedisTemplate redis;
    private ValueOperations<String, String> valores;
    private HashOperations<String, Object, Object> hash;
    private RedisPronosticoProvider proveedor;

    @SuppressWarnings("unchecked")
    @BeforeEach
    void preparar() {
        redis = mock(StringRedisTemplate.class);
        valores = mock(ValueOperations.class);
        hash = mock(HashOperations.class);
        when(redis.opsForValue()).thenReturn(valores);
        when(redis.opsForHash()).thenReturn((HashOperations) hash);
        proveedor = new RedisPronosticoProvider(redis, JsonMapper.builder().build(), "pm25-1", 168);
    }

    @Test
    void leeObservacionesPorClaveYSaltaNulos() {
        when(valores.multiGet(anyCollection())).thenAnswer(inv -> {
            List<String> claves = new ArrayList<>((Collection<String>) inv.getArgument(0));
            List<String> respuesta = new ArrayList<>();
            for (String clave : claves) {
                if (clave.endsWith(":" + EPOCH_EMISION)) {
                    respuesta.add("{\"PM2_5\":15.19,\"fuente_pm25\":\"SENAMHI vía OpenAQ\",\"estacion\":\"SAN_BORJA\"}");
                } else if (clave.endsWith(":" + (EPOCH_EMISION - 3600))) {
                    respuesta.add("{\"PM2_5\":null,\"temperature_2m\":18.1}");   // hora sin medición
                } else {
                    respuesta.add(null);
                }
            }
            return respuesta;
        });

        List<Medicion> r = proveedor.mediciones("SAN_BORJA", EMISION.minusHours(23), EMISION);

        assertThat(r).hasSize(1);
        assertThat(r.get(0).pm25()).isEqualTo(15.19);
        assertThat(r.get(0).fuente()).isEqualTo("SENAMHI vía OpenAQ");
        assertThat(r.get(0).fechaHora().toEpochSecond()).isEqualTo(EPOCH_EMISION);

        @SuppressWarnings("unchecked")
        ArgumentCaptor<Collection<String>> captor = ArgumentCaptor.forClass(Collection.class);
        verify(valores).multiGet(captor.capture());
        assertThat(captor.getValue()).hasSize(24)
                .contains("pm25:observacion:pm25-1:SAN_BORJA:" + EPOCH_EMISION);
    }

    @Test
    void ultimaMedicionBuscaHaciaAtras() {
        when(valores.multiGet(anyCollection())).thenAnswer(inv -> {
            List<String> respuesta = new ArrayList<>();
            for (String clave : (Collection<String>) inv.getArgument(0)) {
                respuesta.add(clave.endsWith(":" + (EPOCH_EMISION - 30 * 3600))
                        ? "{\"PM2_5\":9.5,\"fuente_pm25\":\"SENAMHI\"}" : null);
            }
            return respuesta;
        });

        Optional<Medicion> r = proveedor.ultimaMedicion("SAN_BORJA", EMISION);

        assertThat(r).isPresent();
        assertThat(r.get().pm25()).isEqualTo(9.5);
        assertThat(r.get().fechaHora()).isEqualTo(EMISION.minusHours(30));
    }

    @Test
    void leePronosticoDelHash() {
        when(hash.entries("pm25:pronostico:pm25-1:SAN_BORJA:" + EPOCH_EMISION + ":1h")).thenReturn(Map.of(
                "estacion", "SAN_BORJA",
                "emitido_en", "2024-11-15T08:00:00-05:00",
                "objetivo_en", "2024-11-15T09:00:00-05:00",
                "horizonte_h", "1",
                "modelo", "xgb_historia_mae",
                "version_modelo", "pm25-1",
                "pm25_estimado", "17.81681746482849"));

        Optional<PronosticoDato> r = proveedor.pronostico("SAN_BORJA", EMISION);

        assertThat(r).isPresent();
        assertThat(r.get().pm25Estimado()).isEqualTo(17.81681746482849);
        assertThat(r.get().objetivoEn()).isEqualTo(OffsetDateTime.parse("2024-11-15T09:00:00-05:00"));
        assertThat(r.get().calculadoEn()).isNull();
        assertThat(r.get().horizonteH()).isEqualTo(1);
    }

    @Test
    void sinHashDevuelveVacio() {
        when(hash.entries(anyString())).thenReturn(Map.of());

        assertThat(proveedor.pronostico("SAN_BORJA", EMISION)).isEmpty();
    }

    @Test
    void redisCaidoSeTraduceAProveedorNoDisponible() {
        when(valores.multiGet(anyCollection())).thenThrow(new RedisConnectionFailureException("caído"));

        assertThatThrownBy(() -> proveedor.mediciones("SAN_BORJA", EMISION.minusHours(23), EMISION))
                .isInstanceOf(ProveedorNoDisponibleException.class);
    }
}
