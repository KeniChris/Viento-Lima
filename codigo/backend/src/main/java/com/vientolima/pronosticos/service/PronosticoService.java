package com.vientolima.pronosticos.service;

import com.vientolima.config.RelojConfig;
import com.vientolima.errores.ApiException;
import com.vientolima.estaciones.service.CatalogoEstaciones;
import com.vientolima.pronosticos.dto.ConsultaPronostico;
import com.vientolima.pronosticos.dto.Observacion;
import com.vientolima.pronosticos.dto.Pronostico;
import com.vientolima.pronosticos.dto.UltimaMedicion;
import com.vientolima.pronosticos.provider.Medicion;
import com.vientolima.pronosticos.provider.PronosticoDato;
import com.vientolima.pronosticos.provider.PronosticoProvider;
import com.vientolima.pronosticos.provider.ProveedorNoDisponibleException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeParseException;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Arma la respuesta del dashboard para un único corte de tiempo.
 *
 * <p>modo=actual: el corte es la hora entera actual de Lima. Hay pronóstico VIGENTE si existe lectura en el corte
 * y un pronóstico emitido en ese corte, calculado entre el corte y el reloj de consulta, con objetivo posterior al
 * reloj. Sin lectura en el corte → SIN_DATOS_RECIENTES; con lectura pero sin pronóstico vigente → SIN_PRONOSTICO.
 *
 * <p>modo=historico: el corte es {@code emision}. Con pronóstico → HISTORICO; sin él → SIN_PRONOSTICO.
 * El campo calculado_en no se expone en histórico (la fuente no garantiza ese metadato).
 */
@Service
public class PronosticoService {
    private static final Pattern ID_ESTACION = Pattern.compile("^[A-Z][A-Z0-9_]+$");
    private static final ZoneOffset OFFSET_LIMA = ZoneOffset.ofHours(-5);
    private static final int HORAS = 24;

    private final PronosticoProvider proveedor;
    private final CatalogoEstaciones catalogo;
    private final Clock reloj;

    public PronosticoService(PronosticoProvider proveedor, CatalogoEstaciones catalogo, Clock reloj) {
        this.proveedor = proveedor;
        this.catalogo = catalogo;
        this.reloj = reloj;
    }

    public ConsultaPronostico consultar(String estacion, String modo, String emision) {
        validarEstacion(estacion);
        boolean historico = esHistorico(modo);
        OffsetDateTime ahora = OffsetDateTime.now(reloj).withOffsetSameInstant(OFFSET_LIMA);
        OffsetDateTime corte = historico ? parsearEmision(emision) : actual(emision, ahora);

        try {
            return construir(estacion, historico, corte, ahora);
        } catch (ProveedorNoDisponibleException ex) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "FUENTE_NO_DISPONIBLE",
                    "No pudimos cargar los datos. Intenta de nuevo.");
        }
    }

    private ConsultaPronostico construir(String estacion, boolean historico, OffsetDateTime corte,
                                         OffsetDateTime ahora) {
        OffsetDateTime desde = corte.minusHours(HORAS - 1);
        Map<Instant, Medicion> lecturas = new HashMap<>();
        for (Medicion m : proveedor.mediciones(estacion, desde, corte)) {
            lecturas.put(m.fechaHora().toInstant(), m);
        }

        List<Observacion> observaciones = new ArrayList<>(HORAS);
        for (int i = 0; i < HORAS; i++) {
            OffsetDateTime hora = desde.plusHours(i);
            Medicion m = lecturas.get(hora.toInstant());
            observaciones.add(new Observacion(hora, m == null ? null : m.pm25()));
        }

        Optional<Medicion> ultima = lecturas.values().stream()
                .max(Comparator.comparing(m -> m.fechaHora().toInstant()));
        if (ultima.isEmpty()) {
            ultima = proveedor.ultimaMedicion(estacion, corte);
        }

        String estado;
        Pronostico pronostico = null;
        if (historico) {
            Optional<PronosticoDato> dato = proveedor.pronostico(estacion, corte);
            estado = dato.isPresent() ? "HISTORICO" : "SIN_PRONOSTICO";
            pronostico = dato.map(d -> aDto(d, null)).orElse(null);
        } else if (!lecturas.containsKey(corte.toInstant())) {
            estado = "SIN_DATOS_RECIENTES";
        } else {
            Optional<PronosticoDato> vigente = proveedor.pronostico(estacion, corte)
                    .filter(d -> esVigente(d, corte, ahora));
            estado = vigente.isPresent() ? "VIGENTE" : "SIN_PRONOSTICO";
            pronostico = vigente.map(d -> aDto(d, d.calculadoEn())).orElse(null);
        }

        return new ConsultaPronostico(estacion, "America/Lima", "µg/m³", historico ? "HISTORICO" : "ACTUAL",
                estado, ultima.map(PronosticoService::aDto).orElse(null), pronostico, observaciones);
    }

    private static boolean esVigente(PronosticoDato d, OffsetDateTime corte, OffsetDateTime ahora) {
        boolean mismoCorte = d.emitidoEn().toInstant().equals(corte.toInstant());
        boolean objetivoFuturo = d.objetivoEn().toInstant().isAfter(ahora.toInstant());
        boolean calculadoValido = d.calculadoEn() == null
                || (!d.calculadoEn().isBefore(corte) && !d.calculadoEn().isAfter(ahora));
        return mismoCorte && objetivoFuturo && calculadoValido;
    }

    private static UltimaMedicion aDto(Medicion m) {
        return new UltimaMedicion(lima(m.fechaHora()), m.pm25(), m.fuente());
    }

    private static Pronostico aDto(PronosticoDato d, OffsetDateTime calculado) {
        return new Pronostico(lima(d.emitidoEn()), lima(d.objetivoEn()),
                calculado == null ? null : lima(calculado), d.horizonteH(), d.pm25Estimado(), d.versionModelo());
    }

    private static OffsetDateTime lima(OffsetDateTime t) {
        return t.withOffsetSameInstant(OFFSET_LIMA);
    }

    // ---------- validación de parámetros ----------

    private void validarEstacion(String estacion) {
        if (estacion == null || !ID_ESTACION.matcher(estacion).matches()) {
            throw ApiException.solicitudInvalida("estacion", "Indica un identificador de estación válido.");
        }
        if (catalogo.buscar(estacion).isEmpty()) {
            throw new ApiException(HttpStatus.NOT_FOUND, "ESTACION_NO_ENCONTRADA",
                    "Selecciona una estación del catálogo.");
        }
    }

    private static boolean esHistorico(String modo) {
        if (modo == null || modo.equals("actual")) {
            return false;
        }
        if (modo.equals("historico")) {
            return true;
        }
        throw ApiException.solicitudInvalida("modo", "Usa actual o historico.");
    }

    private static OffsetDateTime actual(String emision, OffsetDateTime ahora) {
        if (emision != null) {
            throw ApiException.solicitudInvalida("emision", "Omite emision en modo actual.");
        }
        return ahora.atZoneSameInstant(RelojConfig.LIMA).truncatedTo(ChronoUnit.HOURS).toOffsetDateTime();
    }

    private static OffsetDateTime parsearEmision(String emision) {
        String mensaje = "Indica una hora entera de Lima con desfase explícito.";
        if (emision == null || emision.isBlank()) {
            throw ApiException.solicitudInvalida("emision", mensaje);
        }
        OffsetDateTime t;
        try {
            t = OffsetDateTime.parse(emision.trim());
        } catch (DateTimeParseException ex) {
            throw ApiException.solicitudInvalida("emision", mensaje);
        }
        if (!t.getOffset().equals(OFFSET_LIMA) || t.getMinute() != 0 || t.getSecond() != 0 || t.getNano() != 0) {
            throw ApiException.solicitudInvalida("emision", mensaje);
        }
        return t;
    }
}
