import { Component, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { HttpErrorResponse } from '@angular/common/http';
import { AuthService } from '../../../core/services/auth.service';

@Component({
  imports: [ReactiveFormsModule, RouterLink, MatButtonModule, MatFormFieldModule, MatInputModule],
  selector: 'app-login',
  styleUrl: './login.css',
  templateUrl: './login.html',
})
export class Login {
  private auth = inject(AuthService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  form = new FormGroup({
    correo: new FormControl('', [Validators.required, Validators.email]),
    contrasena: new FormControl('', Validators.required),
  });

  cargando = signal(false);
  error = signal('');

  // Avisos que llegan por la URL: sesión vencida (401 en el dashboard) o cuenta recién creada
  sesionVencida = this.route.snapshot.queryParamMap.get('motivo') === 'vencida';
  cuentaCreada = this.route.snapshot.queryParamMap.get('registro') === 'ok';

  entrar() {
    this.cargando.set(true);
    this.error.set('');

    this.auth
      .login({
        correo: this.form.value.correo ?? '',
        contrasena: this.form.value.contrasena ?? '',
      })
      .subscribe({
        next: () => {
          this.cargando.set(false);
          this.router.navigateByUrl('/dashboard');
        },
        error: (err: HttpErrorResponse) => {
          this.cargando.set(false);
          this.error.set(
            err.status === 0
              ? 'No hay conexión con el servidor. Intenta de nuevo.'
              : (err.error?.mensaje ?? 'No se pudo iniciar sesión'),
          );
        },
      });
  }
}
