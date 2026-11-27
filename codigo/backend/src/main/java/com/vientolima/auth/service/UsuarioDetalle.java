package com.vientolima.auth.service;

import com.vientolima.auth.entity.Usuario;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;

import java.io.Serial;
import java.io.Serializable;
import java.util.Collection;
import java.util.List;
import java.util.UUID;

/** Usuario autenticado que se guarda en la sesión. */
public class UsuarioDetalle implements UserDetails, Serializable {
    @Serial
    private static final long serialVersionUID = 1L;

    private final UUID id;
    private final String correo;
    private final String contrasenaHash;

    public UsuarioDetalle(Usuario usuario) {
        this.id = usuario.getId();
        this.correo = usuario.getCorreo();
        this.contrasenaHash = usuario.getContrasenaHash();
    }

    public UUID getId() {
        return id;
    }

    public String getCorreo() {
        return correo;
    }

    @Override
    public Collection<? extends GrantedAuthority> getAuthorities() {
        return List.of(new SimpleGrantedAuthority("ROLE_USUARIO"));
    }

    @Override
    public String getPassword() {
        return contrasenaHash;
    }

    @Override
    public String getUsername() {
        return correo;
    }
}
