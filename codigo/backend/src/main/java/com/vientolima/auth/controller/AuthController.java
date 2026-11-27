package com.vientolima.auth.controller;

import com.vientolima.auth.dto.RegistroRequest;
import com.vientolima.auth.dto.UsuarioResponse;
import com.vientolima.auth.service.AuthService;
import com.vientolima.auth.service.UsuarioDetalle;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.web.authentication.logout.SecurityContextLogoutHandler;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Sesión por cookie (JSESSIONID) con protección CSRF.
 * El inicio de sesión (POST /auth/login) lo procesa Spring Security; ver {@code SecurityConfig}.
 */
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {
    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    /** Fuerza a Spring a emitir la cookie XSRF-TOKEN que Angular copia al encabezado X-XSRF-TOKEN. */
    @GetMapping("/csrf")
    public ResponseEntity<Void> csrf(CsrfToken token) {
        token.getToken();
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build();
    }

    @PostMapping("/registro")
    public ResponseEntity<UsuarioResponse> registro(@Valid @RequestBody RegistroRequest solicitud) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .cacheControl(CacheControl.noStore())
                .body(authService.registrar(solicitud));
    }

    @GetMapping("/me")
    public ResponseEntity<UsuarioResponse> me(@AuthenticationPrincipal UsuarioDetalle usuario) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(new UsuarioResponse(usuario.getId(), usuario.getCorreo()));
    }

    /** Sin sesión responde 401 antes de llegar aquí (ver SecurityConfig). */
    @PostMapping("/logout")
    public ResponseEntity<Void> logout(HttpServletRequest request, HttpServletResponse response,
                                       Authentication autenticacion) {
        new SecurityContextLogoutHandler().logout(request, response, autenticacion);
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build();
    }
}
