import { Routes } from '@angular/router';
import { provideCharts, withDefaultRegisterables } from 'ng2-charts';
import { DashboardLayout } from '../../layouts/dashboard-layout/dashboard-layout';

export const DASHBOARD_ROUTES: Routes = [
  {
    path: '',
    component: DashboardLayout,
    children: [
      {
        path: '',
        // Chart.js se carga solo al abrir el dashboard
        providers: [provideCharts(withDefaultRegisterables())],
        loadComponent: () => import('./dashboard/dashboard').then((m) => m.DashboardPage),
      },
    ],
  },
];
