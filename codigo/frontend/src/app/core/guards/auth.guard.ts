import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from '../services/auth.service';

// Protege /dashboard: si la cookie de sesión no es válida, va al login
export const authGuard: CanActivateFn = () => {
  const router = inject(Router);
  return inject(AuthService)
    .restaurarSesion()
    .pipe(map((hayAcceso) => hayAcceso || router.createUrlTree(['/login'])));
};
