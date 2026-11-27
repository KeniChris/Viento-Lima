package com.vientolima.config;

import com.vientolima.auth.dto.UsuarioResponse;
import com.vientolima.auth.service.UsuarioDetalle;
import com.vientolima.errores.ErrorApi;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;
import org.springframework.security.web.csrf.CsrfTokenRequestAttributeHandler;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * Sesión por cookie + CSRF con cookie legible por Angular (XSRF-TOKEN → X-XSRF-TOKEN).
 * Los errores 401/403 usan el formato común {codigo, mensaje}.
 */
@Configuration
public class SecurityConfig {
    private static final String LOGIN = "/api/v1/auth/login";

    @Bean
    SecurityFilterChain securityFilterChain(HttpSecurity http, JsonMapper json) throws Exception {
        http
            .csrf(csrf -> csrf
                .csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse())
                .csrfTokenRequestHandler(new CsrfTokenRequestAttributeHandler()))
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers(HttpMethod.GET, "/api/v1/auth/csrf").permitAll()
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/registro", LOGIN).permitAll()
                .anyRequest().authenticated())
            // El inicio de sesión recibe un formulario (correo, contrasena) y responde JSON.
            // loginPage apunta a la misma URL solo para que Spring no genere su página HTML de login.
            .formLogin(form -> form
                .loginPage(LOGIN)
                .loginProcessingUrl(LOGIN)
                .usernameParameter("correo")
                .passwordParameter("contrasena")
                .successHandler((req, res, auth) -> {
                    UsuarioDetalle usuario = (UsuarioDetalle) auth.getPrincipal();
                    escribir(json, res, HttpStatus.OK, new UsuarioResponse(usuario.getId(), usuario.getCorreo()));
                })
                .failureHandler((req, res, ex) -> escribir(json, res, HttpStatus.UNAUTHORIZED,
                        new ErrorApi("CREDENCIALES_INVALIDAS", "Verifica tu correo y contraseña."))))
            .exceptionHandling(ex -> ex
                .authenticationEntryPoint((req, res, e) -> escribir(json, res, HttpStatus.UNAUTHORIZED,
                        new ErrorApi("SESION_REQUERIDA", "Ingresa para continuar.")))
                .accessDeniedHandler((req, res, e) -> escribir(json, res, HttpStatus.FORBIDDEN,
                        new ErrorApi("CSRF_INVALIDO", "Actualiza la página e inténtalo de nuevo."))));
        return http.build();
    }

    @Bean
    PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    private static void escribir(JsonMapper json, HttpServletResponse res, HttpStatus estado, Object cuerpo)
            throws IOException {
        res.setStatus(estado.value());
        res.setContentType(MediaType.APPLICATION_JSON_VALUE);
        res.setCharacterEncoding(StandardCharsets.UTF_8.name());
        res.setHeader("Cache-Control", "no-store");
        res.getWriter().write(json.writeValueAsString(cuerpo));
    }
}
