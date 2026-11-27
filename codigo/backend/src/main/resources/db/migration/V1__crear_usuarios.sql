CREATE TABLE usuarios (
    id              UUID                     PRIMARY KEY,
    correo          VARCHAR(254)             NOT NULL,
    contrasena_hash VARCHAR(100)             NOT NULL,
    creado_en       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- El correo se guarda normalizado (sin espacios y en minúsculas): el índice garantiza una cuenta por correo.
CREATE UNIQUE INDEX uq_usuarios_correo ON usuarios (correo);
