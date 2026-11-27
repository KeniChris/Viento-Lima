// Contrato: Viento_Lima_API_v1.yaml (ConsultaPronostico). Fechas ISO 8601 con -05:00; PM2.5 en µg/m³.
export type EstadoConsulta = 'VIGENTE' | 'HISTORICO' | 'SIN_DATOS_RECIENTES' | 'SIN_PRONOSTICO';
export type ModoConsulta = 'actual' | 'historico';

export interface UltimaMedicion {
  fecha_hora: string;
  pm25: number;
  fuente: string;
}

export interface Observacion {
  fecha_hora: string;
  pm25: number | null;
}

export interface Pronostico {
  emitido_en: string;
  objetivo_en: string;
  calculado_en: string | null;
  horizonte_h: number;
  pm25_estimado: number;
  version_modelo: string;
}

export interface ConsultaPronostico {
  estacion: string;
  modo: 'ACTUAL' | 'HISTORICO';
  estado: EstadoConsulta;
  zona_horaria: string;
  unidad: string;
  ultima_medicion: UltimaMedicion | null;
  pronostico: Pronostico | null;
  observaciones: Observacion[];
}
