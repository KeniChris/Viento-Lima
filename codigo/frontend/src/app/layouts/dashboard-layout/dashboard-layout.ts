import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatToolbarModule } from '@angular/material/toolbar';
import { AuthService } from '../../core/services/auth.service';

@Component({
  imports: [RouterOutlet, MatButtonModule, MatToolbarModule],
  selector: 'app-dashboard-layout',
  styleUrl: './dashboard-layout.css',
  templateUrl: './dashboard-layout.html',
})
export class DashboardLayout {
  auth = inject(AuthService);
}
