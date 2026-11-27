import { HttpErrorResponse } from '@angular/common/http';
import { DatePipe, DecimalPipe, formatDate, formatNumber } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { ChartConfiguration } from 'chart.js';
import { BaseChartDirective } from 'ng2-charts';
import { Subscription } from 'rxjs';
import { ConsultaPronostico } from '../../../core/models/consulta-pronostico.model';
import { Estacion } from '../../../core/models/estacion.model';
import { EstacionService } from '../../../core/services/estacion.service';
import { PronosticoService } from '../../../core/services/pronostico.service';

// Demostración histórica del contrato: San Borja, 15/11/2024 a las 08:00 de Lima
const ESTACION_DEMO = 'SAN_BORJA';
const EMISION_DEMO = '2024-11-15T08:00:00-05:00';
// Lima no tiene horario de verano: siempre -05:00 (DatePipe no entiende nombres de zona como America/Lima)
const ZONA_LIMA = '-0500';

@Component({
  imports: [
    DatePipe,
    DecimalPipe,
    ReactiveFormsModule,
    BaseChartDirective,
    MatButtonModule,
    MatCardModule,
    MatChipsModule,
    MatFormFieldModule,
    MatIconModule,
    MatProgressBarModule,
    MatSelectModule,
  ],
  selector: 'app-dashboard',
  styleUrl: './dashboard.css',
  templateUrl: './dashboard.html',
})
export class DashboardPage implements OnInit {
  private estacionService = inject(EstacionService);
  private pronosticoService = inject(PronosticoService);
  private peticion?: Subscription;

  zona = ZONA_LIMA;

  form = new FormGroup({
    estacion: new FormControl<string | null>(null, Validators.required),
  });

  estaciones = signal<Estacion[]>([]);
  consulta = signal<ConsultaPronostico | null>(null);
  // true mientras se muestra la demostración histórica
  demostracion = signal(false);
  cargando = signal(false);
  error = signal('');

  // Gráfico de 24 horas + punto del pronóstico; los null de la API quedan como huecos (spanGaps: false)
  grafico = computed<ChartConfiguration<'line'> | null>(() => {
    const c = this.consulta();
    if (!c) return null;

    const hora = (iso: string) => formatDate(iso, 'HH:mm', 'es', ZONA_LIMA);
    const fechaCompleta = (iso: string) => formatDate(iso, "d MMM y, HH:mm", 'es', ZONA_LIMA);
    const fechas = c.observaciones.map((o) => fechaCompleta(o.fecha_hora));
    const etiquetas = c.observaciones.map((o) => hora(o.fecha_hora));
    const medidos: (number | null)[] = c.observaciones.map((o) => o.pm25);
    const pronosticados: (number | null)[] = c.observaciones.map(() => null);

    if (c.pronostico) {
      etiquetas.push(hora(c.pronostico.objetivo_en));
      fechas.push(fechaCompleta(c.pronostico.objetivo_en));
      medidos.push(null);
      pronosticados.push(c.pronostico.pm25_estimado);
    }

    return {
      type: 'line',
      data: {
        labels: etiquetas,
        datasets: [
          {
            label: 'PM2.5 medido',
            data: medidos,
            borderColor: '#1e3a5f',
            backgroundColor: '#1e3a5f',
            pointRadius: 3,
            tension: 0.25,
            spanGaps: false,
          },
          {
            label: 'Pronóstico a 1 h',
            data: pronosticados,
            borderColor: '#0f766e',
            backgroundColor: '#0f766e',
            showLine: false,
            pointStyle: 'rectRot',
            pointRadius: 9,
            pointHoverRadius: 11,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom' },
          tooltip: {
            callbacks: {
              title: (items) => fechas[items[0].dataIndex] ?? '',
              label: (item) =>
                `${item.dataset.label}: ${formatNumber(Number(item.parsed.y), 'es', '1.2-2')} µg/m³`,
            },
          },
        },
        scales: {
          y: { beginAtZero: true, title: { display: true, text: 'PM2.5 (µg/m³)' } },
          x: { title: { display: true, text: 'Hora de Lima' } },
        },
      },
    };
  });

  ngOnInit() {
    this.cargarEstaciones();
    // Al cambiar de estación se actualizan la tarjeta y el gráfico
    this.form.controls.estacion.valueChanges.subscribe(() => this.cargarConsulta());
  }

  cargarEstaciones() {
    this.cargando.set(true);
    this.error.set('');
    this.estacionService.getEstaciones().subscribe({
      next: (respuesta) => {
        this.estaciones.set(respuesta.estaciones);
        const inicial =
          respuesta.estaciones.find((e) => e.id === ESTACION_DEMO) ?? respuesta.estaciones[0];
        this.form.controls.estacion.setValue(inicial?.id ?? null, { emitEvent: false });
        this.cargarConsulta();
      },
      error: (err) => this.fallar(err),
    });
  }

  cargarConsulta() {
    const estacion = this.form.controls.estacion.value;
    if (!estacion) return;
    // Si el usuario cambia de estación mientras carga otra, solo cuenta la última respuesta
    this.peticion?.unsubscribe();
    this.cargando.set(true);
    this.error.set('');
    this.peticion = this.pronosticoService
      .getPronostico(estacion, this.demostracion() ? EMISION_DEMO : null)
      .subscribe({
        next: (consulta) => {
          this.consulta.set(consulta);
          this.cargando.set(false);
        },
        error: (err) => this.fallar(err),
      });
  }

  // Botón "Actualizar" y "Reintentar"
  actualizar() {
    if (this.estaciones().length === 0) {
      this.cargarEstaciones();
    } else {
      this.cargarConsulta();
    }
  }

  alternarDemostracion() {
    const activar = !this.demostracion();
    this.demostracion.set(activar);
    if (activar) {
      this.form.controls.estacion.setValue(ESTACION_DEMO, { emitEvent: false });
    }
    this.cargarConsulta();
  }

  private fallar(err: HttpErrorResponse) {
    this.cargando.set(false);
    // 401: el interceptor ya llevó al login
    if (err.status === 401) return;
    this.consulta.set(null);
    this.error.set(
      err.status === 0
        ? 'No hay conexión con el servidor. Revisa tu conexión e inténtalo de nuevo.'
        : (err.error?.mensaje ?? 'No pudimos cargar los datos. Intenta de nuevo.'),
    );
  }
}
