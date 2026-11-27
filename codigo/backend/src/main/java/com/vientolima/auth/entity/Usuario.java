package com.vientolima.auth.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "usuarios")
public class Usuario {
    @Id
    private UUID id;

    /** Correo normalizado: sin espacios y en minúsculas. */
    @Column(nullable = false, unique = true, length = 254)
    private String correo;

    @Column(name = "contrasena_hash", nullable = false, length = 100)
    private String contrasenaHash;

    @Column(name = "creado_en", nullable = false)
    private Instant creadoEn;

    protected Usuario() {
    }

    public Usuario(String correo, String contrasenaHash) {
        this.id = UUID.randomUUID();
        this.correo = correo;
        this.contrasenaHash = contrasenaHash;
        this.creadoEn = Instant.now();
    }

    public UUID getId() {
        return id;
    }

    public String getCorreo() {
        return correo;
    }

    public String getContrasenaHash() {
        return contrasenaHash;
    }

    public Instant getCreadoEn() {
        return creadoEn;
    }
}
