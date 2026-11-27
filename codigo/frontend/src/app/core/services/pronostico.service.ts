import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { environment } from '../../../environments/environment';
import { ConsultaPronostico } from '../models/consulta-pronostico.model';

@Injectable({ providedIn: 'root' })
export class PronosticoService {
  private http = inject(HttpClient);

  // modo actual: sin emision. modo historico: emision con -05:00 y hora entera de Lima
  getPronostico(estacion: string, emision: string | null = null) {
    let params = new HttpParams().set('estacion', estacion).set('modo', emision ? 'historico' : 'actual');
    if (emision) params = params.set('emision', emision);
    return this.http.get<ConsultaPronostico>(`${environment.apiUrl}/pronosticos`, { params });
  }
}
