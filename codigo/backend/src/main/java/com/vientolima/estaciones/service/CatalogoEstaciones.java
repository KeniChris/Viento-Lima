package com.vientolima.estaciones.service;

import com.vientolima.estaciones.dto.Estacion;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Optional;

/** Catálogo de las siete estaciones del SENAMHI. Los identificadores coinciden con las claves de Redis. */
@Service
public class CatalogoEstaciones {
    private static final List<Estacion> ESTACIONES = List.of(
            new Estacion("CAMPO_DE_MARTE", "Campo de Marte", "Jesús María"),
            new Estacion("CARABAYLLO", "Carabayllo", "Carabayllo"),
            new Estacion("SAN_BORJA", "San Borja", "San Borja"),
            new Estacion("SAN_JUAN_DE_LURIGANCHO", "San Juan de Lurigancho", "San Juan de Lurigancho"),
            new Estacion("SAN_MARTIN_DE_PORRES", "San Martín de Porres", "San Martín de Porres"),
            new Estacion("SANTA_ANITA", "Santa Anita", "Santa Anita"),
            new Estacion("VILLA_MARIA_DEL_TRIUNFO", "Villa María del Triunfo", "Villa María del Triunfo"));

    public List<Estacion> listar() {
        return ESTACIONES;
    }

    public Optional<Estacion> buscar(String id) {
        return ESTACIONES.stream().filter(e -> e.id().equals(id)).findFirst();
    }
}
