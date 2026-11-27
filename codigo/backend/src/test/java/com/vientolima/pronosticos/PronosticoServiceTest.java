package com.vientolima.pronosticos;

import com.vientolima.config.RelojConfig;
import com.vientolima.errores.ApiException;
import com.vientolima.estaciones.service.CatalogoEstaciones;
import com.vientolima.pronosticos.dto.ConsultaPronostico;
import com.vientolima.pronosticos.provider.FixturePronosticoProvider;
import com.vientolima.pronosticos.provider.PronosticoProvider;
import com.vientolima.pronosticos.provider.ProveedorNoDisponibleException;
import com.vientolima.pronosticos.service.PronosticoService;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

import java.time.Clock;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PronosticoServiceTest {
    private static final String DEMO = "2024-11-15T08:00:00-05:00";

    private static PronosticoService servicio(String relojFijo) {
        Clock reloj = Clock.fixed(OffsetDateTime.parse(relojFijo).toInstant(), RelojConfig.LIMA);
        return new PronosticoService(new FixturePronosticoProvider(reloj), new CatalogoEstaciones(), reloj);
    }

    @Test
    void vigenteConReloj0815() {
        ConsultaPronostico r = servicio("2024-11-15T08:15:00-05:00").consultar("SAN_BORJA", "actual", null);

        assertThat(r.estado()).isEqualTo("VIGENTE");
        assertThat(r.modo()).isEqualTo("ACTUAL");
        assertThat(r.ultimaMedicion().pm25()).isEqualTo(15.19);
        assertThat(r.ultimaMedicion().fechaHora()).isEqualTo(OffsetDateTime.parse(DEMO));
        assertThat(r.pronostico().pm25Estimado()).isEqualTo(17.81681746482849);
        assertThat(r.pronostico().objetivoEn()).isEqualTo(OffsetDateTime.parse("2024-11-15T09:00:00-05:00"));
        assertThat(r.pronostico().calculadoEn()).isEqualTo(OffsetDateTime.parse("2024-11-15T08:12:00-05:00"));
        assertThat(r.observaciones()).hasSize(24);
    }

    @Test
    void historicoDeLaDemostracionCoincideConElContrato() {
        ConsultaPronostico r = servicio("2024-11-15T08:15:00-05:00").consultar("SAN_BORJA", "historico", DEMO);

        assertThat(r.estado()).isEqualTo("HISTORICO");
        assertThat(r.pronostico().calculadoEn()).isNull();
        assertThat(r.pronostico().horizonteH()).isEqualTo(1);
        assertThat(r.pronostico().versionModelo()).isEqualTo("pm25-1");
        assertThat(r.observaciones().get(0).fechaHora())
                .isEqualTo(OffsetDateTime.parse("2024-11-14T09:00:00-05:00"));
        assertThat(r.observaciones().get(0).pm25()).isEqualTo(24.74);
        assertThat(r.observaciones().get(17).pm25()).isNull();   // hueco a las 02:00
        assertThat(r.observaciones().get(23).pm25()).isEqualTo(15.19);
        assertThat(r.observaciones().get(23).fechaHora()).isEqualTo(OffsetDateTime.parse(DEMO));
    }

    @Test
    void sinPronosticoCuandoHayLecturaPeroNoCalculo() {
        ConsultaPronostico r = servicio("2024-11-15T08:15:00-05:00").consultar("CAMPO_DE_MARTE", "actual", null);

        assertThat(r.estado()).isEqualTo("SIN_PRONOSTICO");
        assertThat(r.pronostico()).isNull();
        assertThat(r.ultimaMedicion()).isNotNull();
    }

    @Test
    void sinDatosRecientesCuandoLaEstacionNoTieneLecturas() {
        ConsultaPronostico r = servicio("2025-01-01T10:15:00-05:00").consultar("CARABAYLLO", "actual", null);

        assertThat(r.estado()).isEqualTo("SIN_DATOS_RECIENTES");
        assertThat(r.ultimaMedicion()).isNull();
        assertThat(r.pronostico()).isNull();
        assertThat(r.observaciones()).hasSize(24).allMatch(o -> o.pm25() == null);
        assertThat(r.observaciones().get(0).fechaHora()).isEqualTo(OffsetDateTime.parse("2024-12-31T11:00:00-05:00"));
    }

    @Test
    void sinDatosRecientesSiFaltaLaLecturaDelCorte() {
        // A las 02:xx la hora 02:00 no tiene lectura: hay última medición anterior, pero no pronóstico.
        ConsultaPronostico r = servicio("2024-11-15T02:20:00-05:00").consultar("SAN_BORJA", "actual", null);

        assertThat(r.estado()).isEqualTo("SIN_DATOS_RECIENTES");
        assertThat(r.pronostico()).isNull();
        assertThat(r.ultimaMedicion().fechaHora()).isEqualTo(OffsetDateTime.parse("2024-11-15T01:00:00-05:00"));
    }

    @Test
    void estacionInexistenteDevuelve404() {
        assertThatThrownBy(() -> servicio("2024-11-15T08:15:00-05:00").consultar("NO_EXISTE", "actual", null))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getEstado()).isEqualTo(HttpStatus.NOT_FOUND);
                    assertThat(e.getCodigo()).isEqualTo("ESTACION_NO_ENCONTRADA");
                });
    }

    @Test
    void parametrosInvalidosDevuelven400() {
        PronosticoService s = servicio("2024-11-15T08:15:00-05:00");
        assertCodigo400(() -> s.consultar("san borja", "actual", null));
        assertCodigo400(() -> s.consultar("SAN_BORJA", "otro", null));
        assertCodigo400(() -> s.consultar("SAN_BORJA", "historico", null));
        assertCodigo400(() -> s.consultar("SAN_BORJA", "historico", "2024-11-15T08:30:00-05:00"));
        assertCodigo400(() -> s.consultar("SAN_BORJA", "historico", "2024-11-15T13:00:00Z"));
        assertCodigo400(() -> s.consultar("SAN_BORJA", "historico", "ayer"));
        assertCodigo400(() -> s.consultar("SAN_BORJA", "actual", DEMO));
    }

    @Test
    void fuenteCaidaDevuelve503() {
        Clock reloj = Clock.fixed(OffsetDateTime.parse("2024-11-15T08:15:00-05:00").toInstant(), RelojConfig.LIMA);
        PronosticoProvider caido = new FixturePronosticoProvider(reloj) {
            @Override
            public java.util.List<com.vientolima.pronosticos.provider.Medicion> mediciones(
                    String estacion, OffsetDateTime desde, OffsetDateTime hasta) {
                throw new ProveedorNoDisponibleException("Redis no responde", null);
            }
        };
        PronosticoService s = new PronosticoService(caido, new CatalogoEstaciones(), reloj);

        assertThatThrownBy(() -> s.consultar("SAN_BORJA", "actual", null))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getEstado()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
                    assertThat(e.getCodigo()).isEqualTo("FUENTE_NO_DISPONIBLE");
                });
    }

    private static void assertCodigo400(org.assertj.core.api.ThrowableAssert.ThrowingCallable llamada) {
        assertThatThrownBy(llamada).isInstanceOfSatisfying(ApiException.class, e -> {
            assertThat(e.getEstado()).isEqualTo(HttpStatus.BAD_REQUEST);
            assertThat(e.getCodigo()).isEqualTo("SOLICITUD_INVALIDA");
        });
    }
}
