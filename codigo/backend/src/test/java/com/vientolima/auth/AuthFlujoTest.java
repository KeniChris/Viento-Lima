package com.vientolima.auth;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** Recorrido de la API con H2 y datos de prueba: registro, ingreso, sesión, consultas y cierre. */
@SpringBootTest
@ActiveProfiles("test")
class AuthFlujoTest {
    private static final String CLAVE = "UnaClaveDeEjemplo24";

    @Autowired
    WebApplicationContext contexto;
    MockMvc mvc;

    @BeforeEach
    void preparar() {
        mvc = MockMvcBuilders.webAppContextSetup(contexto).apply(springSecurity()).build();
    }

    private void registrar(String correo) throws Exception {
        mvc.perform(post("/api/v1/auth/registro").with(csrf()).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"correo\":\"" + correo + "\",\"contrasena\":\"" + CLAVE + "\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").exists())
                .andExpect(jsonPath("$.correo").value(correo.trim().toLowerCase()));
    }

    private MockHttpSession ingresar(String correo) throws Exception {
        MvcResult r = mvc.perform(post("/api/v1/auth/login").with(csrf())
                        .param("correo", correo).param("contrasena", CLAVE))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.correo").value(correo))
                .andReturn();
        return (MockHttpSession) r.getRequest().getSession(false);
    }

    @Test
    void registroIngresoSesionPronosticoYCierre() throws Exception {
        registrar("flujo@example.com");
        MockHttpSession sesion = ingresar("flujo@example.com");

        mvc.perform(get("/api/v1/auth/me").session(sesion))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.correo").value("flujo@example.com"));
        mvc.perform(get("/api/v1/estaciones").session(sesion))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.estaciones.length()").value(7));
        mvc.perform(get("/api/v1/pronosticos").param("estacion", "SAN_BORJA")
                        .param("modo", "historico").param("emision", "2024-11-15T08:00:00-05:00")
                        .session(sesion))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.estado").value("HISTORICO"))
                .andExpect(jsonPath("$.ultima_medicion.pm25").value(15.19))
                .andExpect(jsonPath("$.pronostico.pm25_estimado").value(17.81681746482849))
                .andExpect(jsonPath("$.pronostico.calculado_en").value(org.hamcrest.Matchers.nullValue()))
                .andExpect(jsonPath("$.observaciones.length()").value(24))
                .andExpect(jsonPath("$.observaciones[17].pm25").value(org.hamcrest.Matchers.nullValue()));
        mvc.perform(post("/api/v1/auth/logout").with(csrf()).session(sesion))
                .andExpect(status().isNoContent());
    }

    @Test
    void correoDuplicadoDevuelve409() throws Exception {
        registrar("duplicado@example.com");
        mvc.perform(post("/api/v1/auth/registro").with(csrf()).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"correo\":\" DUPLICADO@Example.com \",\"contrasena\":\"" + CLAVE + "\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.codigo").value("CORREO_EN_USO"));
    }

    @Test
    void registroInvalidoDevuelve400ConCampos() throws Exception {
        mvc.perform(post("/api/v1/auth/registro").with(csrf()).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"correo\":\"no-es-correo\",\"contrasena\":\"corta\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.codigo").value("SOLICITUD_INVALIDA"))
                .andExpect(jsonPath("$.campos.correo").isArray())
                .andExpect(jsonPath("$.campos.contrasena").isArray());
    }

    @Test
    void credencialesIncorrectasDevuelven401() throws Exception {
        mvc.perform(post("/api/v1/auth/login").with(csrf())
                        .param("correo", "nadie@example.com").param("contrasena", CLAVE))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.codigo").value("CREDENCIALES_INVALIDAS"));
    }

    @Test
    void sinSesionDevuelve401() throws Exception {
        mvc.perform(get("/api/v1/auth/me"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.codigo").value("SESION_REQUERIDA"));
        mvc.perform(get("/api/v1/pronosticos").param("estacion", "SAN_BORJA"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void sinTokenCsrfDevuelve403() throws Exception {
        mvc.perform(post("/api/v1/auth/registro").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"correo\":\"csrf@example.com\",\"contrasena\":\"" + CLAVE + "\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.codigo").value("CSRF_INVALIDO"));
    }

    @Test
    void csrfDevuelve204() throws Exception {
        mvc.perform(get("/api/v1/auth/csrf")).andExpect(status().isNoContent());
    }
}
