import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';

// La autenticación va en la cookie de sesión (la envía el navegador); aquí solo se atiende la sesión vencida.
export const authInterceptor: HttpInterceptorFn = (peticion, siguiente) => {
  const auth = inject(AuthService);
  // Estas llamadas manejan su propio 401 (credenciales incorrectas, "no hay sesión al abrir la página")
  const esAcceso = peticion.url.includes('/auth/');

  return siguiente(peticion).pipe(
    catchError((err) => {
      if (err.status === 401 && !esAcceso) {
        auth.sesionVencida();
      }
      return throwError(() => err);
    }),
  );
};
