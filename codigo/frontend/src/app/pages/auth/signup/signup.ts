import { Component, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { HttpErrorResponse } from '@angular/common/http';
import { AuthService } from '../../../core/services/auth.service';

// La confirmación debe ser igual a la contraseña
function coincideConContrasena(control: AbstractControl): ValidationErrors | null {
  const contrasena = control.parent?.get('contrasena')?.value;
  return control.value === contrasena ? null : { noCoincide: true };
}

@Component({
  imports: [ReactiveFormsModule, RouterLink, MatButtonModule, MatFormFieldModule, MatInputModule],
  selector: 'app-signup',
  styleUrl: './signup.css',
  templateUrl: './signup.html',
})
export class Signup {
  private auth = inject(AuthService);
  private router = inject(Router);

  form = new FormGroup({
    correo: new FormControl('', [Validators.required, Validators.email, Validators.maxLength(254)]),
    // La API pide de 10 a 72 caracteres
    contrasena: new FormControl('', [Validators.required, Validators.minLength(10), Validators.maxLength(72)]),
    confirmar: new FormControl('', [Validators.required, coincideConContrasena]),
  });

  cargando = signal(false);
  error = signal('');

  constructor() {
    // Si cambia la contraseña, la confirmación se vuelve a comprobar
    this.form.controls.contrasena.valueChanges.subscribe(() =>
      this.form.controls.confirmar.updateValueAndValidity(),
    );
  }

  crear() {
    this.cargando.set(true);
    this.error.set('');

    this.auth
      .registrar({
        correo: this.form.value.correo ?? '',
        contrasena: this.form.value.contrasena ?? '',
      })
      .subscribe({
        next: () => {
          this.cargando.set(false);
          this.router.navigate(['/login'], { queryParams: { registro: 'ok' } });
        },
        error: (err: HttpErrorResponse) => {
          this.cargando.set(false);
          if (err.error?.codigo === 'CORREO_EN_USO') {
            // El error queda en el campo hasta que el usuario cambie el correo
            this.form.controls.correo.setErrors({ duplicado: true });
            this.form.controls.correo.markAsTouched();
          } else {
            this.error.set(
              err.status === 0
                ? 'No hay conexión con el servidor. Intenta de nuevo.'
                : (err.error?.mensaje ?? 'No se pudo crear la cuenta'),
            );
          }
        },
      });
  }
}
