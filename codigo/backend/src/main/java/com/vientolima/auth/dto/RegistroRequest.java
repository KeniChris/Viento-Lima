package com.vientolima.auth.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record RegistroRequest(
        @NotBlank(message = "Ingresa tu correo.")
        @Email(message = "Ingresa un correo válido.")
        @Size(max = 254, message = "El correo admite como máximo 254 caracteres.")
        String correo,

        @NotBlank(message = "Ingresa una contraseña.")
        @Size(min = 10, max = 72, message = "La contraseña debe tener entre 10 y 72 caracteres.")
        String contrasena) {
    public RegistroRequest {
        if (correo != null) {
            correo = correo.trim();
        }
    }
}
