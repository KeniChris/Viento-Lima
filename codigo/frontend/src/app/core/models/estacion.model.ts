export interface Estacion {
  id: string;
  nombre: string;
  distrito: string;
}

export interface EstacionesResponse {
  estaciones: Estacion[];
}
