package com.vientolima.auth.service;

import com.vientolima.auth.dto.RegistroRequest;
import com.vientolima.auth.dto.UsuarioResponse;
import com.vientolima.auth.entity.Usuario;
import com.vientolima.auth.repository.UsuarioRepository;
import com.vientolima.errores.ApiException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.util.Locale;

@Service
public class AuthService {
    /** BCrypt solo usa los primeros 72 bytes de la contraseña. */
    private static final int MAX_BYTES_CONTRASENA = 72;

    private final UsuarioRepository usuarios;
    private final PasswordEncoder codificador;

    public AuthService(UsuarioRepository usuarios, PasswordEncoder codificador) {
        this.usuarios = usuarios;
        this.codificador = codificador;
    }

    public UsuarioResponse registrar(RegistroRequest solicitud) {
        if (solicitud.contrasena().getBytes(StandardCharsets.UTF_8).length > MAX_BYTES_CONTRASENA) {
            throw ApiException.solicitudInvalida("contrasena", "La contraseña admite como máximo 72 bytes.");
        }
        String correo = normalizar(solicitud.correo());
        if (usuarios.existsByCorreo(correo)) {
            throw correoEnUso();
        }
        try {
            Usuario usuario = usuarios.saveAndFlush(new Usuario(correo, codificador.encode(solicitud.contrasena())));
            return new UsuarioResponse(usuario.getId(), usuario.getCorreo());
        } catch (DataIntegrityViolationException ex) {
            // Dos registros simultáneos con el mismo correo: gana el índice único.
            throw correoEnUso();
        }
    }

    public static String normalizar(String correo) {
        return correo.trim().toLowerCase(Locale.ROOT);
    }

    private ApiException correoEnUso() {
        return new ApiException(HttpStatus.CONFLICT, "CORREO_EN_USO", "Ese correo ya está registrado.");
    }
}
