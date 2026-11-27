package com.vientolima.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneId;

@Configuration
public class RelojConfig {
    public static final ZoneId LIMA = ZoneId.of("America/Lima");

    /**
     * Reloj de la aplicación. Con app.pm25.reloj-fijo (PM25_RELOJ_FIJO) se congela para la demostración
     * con datos de prueba; vacío usa la hora real de Lima.
     */
    @Bean
    Clock reloj(@Value("${app.pm25.reloj-fijo:}") String fijo) {
        if (fijo == null || fijo.isBlank()) {
            return Clock.system(LIMA);
        }
        return Clock.fixed(OffsetDateTime.parse(fijo.trim()).toInstant(), LIMA);
    }
}
