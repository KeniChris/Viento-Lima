import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable, signal } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, map, Observable, of, switchMap, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { LoginRequest } from '../models/login-request.model';
import { RegisterRequest } from '../models/register-request.model';
import { Usuario } from '../models/usuario.model';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);
  private router = inject(Router);

  // null = nadie ha iniciado sesión. La sesión vive en la cookie JSESSIONID (HttpOnly), no en el navegador.
  session = signal<Usuario | null>(null);
  // Ya se preguntó a /auth/me al abrir la página (evita repetir la consulta en cada navegación)
  private restaurada = false;

  // Pide a Spring la cookie XSRF-TOKEN; Angular la copia a X-XSRF-TOKEN en el siguiente POST
  csrf() {
    return this.http.get<void>(`${environment.apiUrl}/auth/csrf`);
  }

  // Crea la cuenta; no inicia sesión: después el usuario entra por /login
  registrar(datos: RegisterRequest) {
    return this.csrf().pipe(
      switchMap(() => this.http.post<Usuario>(`${environment.apiUrl}/auth/registro`, datos)),
    );
  }

  login(datos: LoginRequest) {
    // El inicio de sesión es un formulario (application/x-www-form-urlencoded), no JSON
    const cuerpo = new HttpParams().set('correo', datos.correo).set('contrasena', datos.contrasena);
    return this.csrf().pipe(
      switchMap(() => this.http.post<Usuario>(`${environment.apiUrl}/auth/login`, cuerpo)),
      // Tras iniciar sesión Spring renueva el token CSRF: se pide el nuevo antes del siguiente POST
      switchMap((usuario) => this.csrf().pipe(map(() => usuario))),
      tap((usuario) => {
        this.session.set(usuario);
        this.restaurada = true;
      }),
    );
  }

  // Al abrir o recargar la página: recupera el usuario de la sesión con /auth/me
  restaurarSesion(): Observable<boolean> {
    if (this.session()) return of(true);
    if (this.restaurada) return of(false);
    return this.csrf().pipe(
      switchMap(() => this.http.get<Usuario>(`${environment.apiUrl}/auth/me`)),
      tap((usuario) => this.session.set(usuario)),
      map(() => true),
      catchError(() => of(false)),
      tap(() => (this.restaurada = true)),
    );
  }

  logout() {
    this.http
      .post<void>(`${environment.apiUrl}/auth/logout`, null)
      .pipe(catchError(() => of(null)))
      .subscribe(() => this.terminarSesion());
  }

  // La API respondió 401 con una sesión que ya no existe
  sesionVencida() {
    this.session.set(null);
    this.restaurada = true;
    this.router.navigate(['/login'], { queryParams: { motivo: 'vencida' } });
  }

  private terminarSesion() {
    this.session.set(null);
    this.restaurada = true;
    this.router.navigateByUrl('/login');
  }
}
