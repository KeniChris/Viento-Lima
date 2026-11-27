import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  {
    path: 'dashboard',
    canActivate: [authGuard],
    loadChildren: () => import('./pages/dashboard/dashboard.routes').then((m) => m.DASHBOARD_ROUTES),
  },
  { path: '', loadChildren: () => import('./pages/auth/auth.routes').then((m) => m.AUTH_ROUTES) },
  { path: '**', redirectTo: 'dashboard' },
];
