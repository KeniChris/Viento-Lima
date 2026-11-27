package com.vientolima.pronosticos.controller;

import com.vientolima.pronosticos.dto.ConsultaPronostico;
import com.vientolima.pronosticos.service.PronosticoService;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/pronosticos")
public class PronosticoController {
    private final PronosticoService servicio;

    public PronosticoController(PronosticoService servicio) {
        this.servicio = servicio;
    }

    @GetMapping
    public ResponseEntity<ConsultaPronostico> consultar(
            @RequestParam String estacion,
            @RequestParam(required = false) String modo,
            @RequestParam(required = false) String emision) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(servicio.consultar(estacion, modo, emision));
    }
}
