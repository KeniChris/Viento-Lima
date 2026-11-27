import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { environment } from '../../../environments/environment';
import { EstacionesResponse } from '../models/estacion.model';

@Injectable({ providedIn: 'root' })
export class EstacionService {
  private http = inject(HttpClient);

  getEstaciones() {
    return this.http.get<EstacionesResponse>(`${environment.apiUrl}/estaciones`);
  }
}
