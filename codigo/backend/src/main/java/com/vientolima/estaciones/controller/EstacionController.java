package com.vientolima.estaciones.controller;

import com.vientolima.estaciones.dto.EstacionesResponse;
import com.vientolima.estaciones.service.CatalogoEstaciones;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/estaciones")
public class EstacionController {
    private final CatalogoEstaciones catalogo;

    public EstacionController(CatalogoEstaciones catalogo) {
        this.catalogo = catalogo;
    }

    @GetMapping
    public EstacionesResponse listar() {
        return new EstacionesResponse(catalogo.listar());
    }
}
