import React, { useState, useEffect } from 'react';
import { fetchMetrics } from './api';

export default function App() {
  const [metrics, setMetrics] = useState({ users: 1420, revenue: 58200, uptime: '99.98%' });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchMetrics().then(data => setMetrics(data));
  }, []);

  return (
    <div className="dashboard-container">
      <header className="dashboard-header">
        <h1>NR AI SaaS Dashboard</h1>
        <span className="badge">Live Status</span>
      </header>
      <main className="metrics-grid">
        <div className="card">
          <h3>Active Users</h3>
          <p className="stat">{metrics.users}</p>
        </div>
        <div className="card">
          <h3>Total Revenue</h3>
          <p className="stat">${metrics.revenue}</p>
        </div>
        <div className="card">
          <h3>System Uptime</h3>
          <p className="stat">{metrics.uptime}</p>
        </div>
      </main>
    </div>
  );
}
