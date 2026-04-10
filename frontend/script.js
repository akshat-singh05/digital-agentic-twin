/* ═══════════════════════════════════════════════════════════
   Agentic Digital Twin — Dashboard Application
   ═══════════════════════════════════════════════════════════ */

const API = 'http://127.0.0.1:8000/api';

// ── State ────────────────────────────────────────────────
let currentPage  = 'dashboard';
let selectedUser = null;
let usersCache   = [];

// ═════════════════════════════════════════════════════════
//  API HELPERS
// ═════════════════════════════════════════════════════════
async function api(path, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);

  const res  = await fetch(`${API}${path}`, opts);
  const json = await res.json();

  if (!res.ok) {
    const msg = json?.message || json?.detail?.message || JSON.stringify(json?.detail) || 'Request failed';
    throw new Error(msg);
  }
  return json;
}

async function apiGet(path)          { return api(path, 'GET'); }
async function apiPost(path, body)   { return api(path, 'POST', body); }

// ═════════════════════════════════════════════════════════
//  TOAST NOTIFICATIONS
// ═════════════════════════════════════════════════════════
function toast(type, title, message) {
  const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `
    <span class="toast-icon">${icons[type] || 'ℹ️'}</span>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      ${message ? `<div class="toast-message">${message}</div>` : ''}
    </div>
    <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
  `;
  container.appendChild(el);
  setTimeout(() => { el.classList.add('toast-out'); setTimeout(() => el.remove(), 300); }, 4000);
}

// ═════════════════════════════════════════════════════════
//  HELPERS
// ═════════════════════════════════════════════════════════
function $(id) { return document.getElementById(id); }
function html(id, content) { $(id).innerHTML = content; }
function esc(str) { const d = document.createElement('div'); d.textContent = str; return d.innerHTML; }

function loading(text = 'Loading...') {
  return `<div class="loading-overlay"><div class="loading-spinner"></div><div class="loading-text">${text}</div></div>`;
}

function emptyState(icon, title, desc) {
  return `<div class="empty-state"><div class="empty-icon">${icon}</div><h3>${title}</h3><p>${desc}</p></div>`;
}

function badge(text, type = 'info') {
  return `<span class="card-badge badge-${type}">${esc(text)}</span>`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) + ' ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

function effClass(eff) {
  if (eff < 0.4)  return 'low';
  if (eff <= 0.8) return 'good';
  return 'high';
}

function requireUser() {
  if (!selectedUser) {
    toast('warning', 'Select a User', 'Please select a user from the dropdown first.');
    return false;
  }
  return true;
}

// ═════════════════════════════════════════════════════════
//  NAVIGATION
// ═════════════════════════════════════════════════════════
const pageTitles = {
  dashboard:     'Dashboard',
  users:         'Users',
  subscriptions: 'Subscriptions',
  usage:         'Usage Data',
  analysis:      'Usage Analysis',
  negotiation:   'Negotiation Agent',
  switching:     'Plan Switching',
  audit:         'Audit Logs',
};

function navigate(page) {
  currentPage = page;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${page}"]`)?.classList.add('active');
  $('pageTitle').textContent = pageTitles[page] || page;
  renderPage(page);
  // close sidebar on mobile
  $('sidebar').classList.remove('open');
}

function toggleSidebar() {
  $('sidebar').classList.toggle('open');
}

// ── Nav click handlers ──
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => navigate(item.dataset.page));
});

// ═════════════════════════════════════════════════════════
//  USER SELECTOR
// ═════════════════════════════════════════════════════════
async function loadUsers() {
  try {
    const res = await apiGet('/users');
    usersCache = res.data || [];
    const sel = $('globalUserSelect');
    sel.innerHTML = '<option value="">— Select User —</option>' +
      usersCache.map(u => `<option value="${u.id}">${u.name} (ID: ${u.id})</option>`).join('');
    if (usersCache.length > 0) {
      sel.value = usersCache[0].id;
      selectedUser = usersCache[0].id;
    }
  } catch (e) {
    toast('error', 'Failed to load users', e.message);
  }
}

function onUserChange() {
  const v = $('globalUserSelect').value;
  selectedUser = v ? parseInt(v) : null;
  // Re-render the current page when user changes
  renderPage(currentPage);
}

// ═════════════════════════════════════════════════════════
//  SERVER HEALTH CHECK
// ═════════════════════════════════════════════════════════
async function checkServer() {
  try {
    await fetch('http://127.0.0.1:8000/');
    $('serverDot').style.background = 'var(--success)';
    $('serverDot').style.boxShadow = '0 0 8px rgba(16,185,129,0.5)';
    $('serverLabel').textContent = 'Server Online';
  } catch {
    $('serverDot').style.background = 'var(--danger)';
    $('serverDot').style.boxShadow = '0 0 8px rgba(239,68,68,0.5)';
    $('serverLabel').textContent = 'Server Offline';
  }
}

// ═════════════════════════════════════════════════════════
//  PAGE ROUTER
// ═════════════════════════════════════════════════════════
function renderPage(page) {
  const body = $('contentBody');
  body.innerHTML = loading();

  const renderers = {
    dashboard:     renderDashboard,
    users:         renderUsers,
    subscriptions: renderSubscriptions,
    usage:         renderUsage,
    analysis:      renderAnalysis,
    negotiation:   renderNegotiation,
    switching:     renderSwitching,
    audit:         renderAudit,
  };

  (renderers[page] || renderDashboard)();
}

// ═════════════════════════════════════════════════════════
//  DASHBOARD PAGE
// ═════════════════════════════════════════════════════════
async function renderDashboard() {
  const body = $('contentBody');
  try {
    const usersRes = await apiGet('/users');
    const users = usersRes.data || [];
    let totalSubs = 0, activeSubs = 0;

    // Count subscriptions for all users
    for (const u of users.slice(0, 10)) {
      try {
        const subsRes = await apiGet(`/subscriptions/user/${u.id}`);
        const subs = subsRes.data || [];
        totalSubs += subs.length;
        activeSubs += subs.filter(s => s.is_active).length;
      } catch { /* skip */ }
    }

    body.innerHTML = `
      <div class="page-section">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-icon purple">👥</div>
            <div class="stat-info">
              <div class="stat-label">Total Users</div>
              <div class="stat-value">${users.length}</div>
              <div class="stat-change up">↑ Active</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon blue">📱</div>
            <div class="stat-info">
              <div class="stat-label">Total Subscriptions</div>
              <div class="stat-value">${totalSubs}</div>
              <div class="stat-change up">${activeSubs} active</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon green">🤖</div>
            <div class="stat-info">
              <div class="stat-label">AI Modules</div>
              <div class="stat-value">5</div>
              <div class="stat-change up">All Online</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon cyan">⚡</div>
            <div class="stat-info">
              <div class="stat-label">Pipeline Status</div>
              <div class="stat-value">Ready</div>
              <div class="stat-change up">Operational</div>
            </div>
          </div>
        </div>

        <!-- Pipeline Visual -->
        <div class="card">
          <div class="card-header">
            <h3>⚡ Full Automation Pipeline</h3>
            ${selectedUser ? `<button class="btn btn-primary" onclick="runFullCycle()" id="btnCycle">
              🚀 Run Full Cycle for User ${selectedUser}
            </button>` : `<span style="color:var(--text-muted);font-size:0.82rem">Select a user to run</span>`}
          </div>
          <div class="pipeline-steps" id="pipelineSteps">
            <div class="pipeline-step" id="ps-analyze">📊 Analyze</div>
            <span class="pipeline-arrow">→</span>
            <div class="pipeline-step" id="ps-sanitize">🔒 Sanitize</div>
            <span class="pipeline-arrow">→</span>
            <div class="pipeline-step" id="ps-negotiate">🤝 Negotiate</div>
            <span class="pipeline-arrow">→</span>
            <div class="pipeline-step" id="ps-switch">🔄 Switch</div>
            <span class="pipeline-arrow">→</span>
            <div class="pipeline-step" id="ps-audit">📝 Audit</div>
          </div>
          <div id="cycleResult"></div>
        </div>

        <!-- Recent Users -->
        <div class="card" style="margin-top:20px">
          <div class="card-header">
            <h3>👤 Recent Users</h3>
            <button class="btn btn-secondary btn-sm" onclick="navigate('users')">View All →</button>
          </div>
          <div class="table-wrapper">
            <table>
              <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Joined</th></tr></thead>
              <tbody>
                ${users.slice(0, 5).map(u => `
                  <tr>
                    <td><strong>${u.id}</strong></td>
                    <td>${esc(u.name)}</td>
                    <td style="color:var(--text-secondary)">${esc(u.email)}</td>
                    <td style="color:var(--text-muted)">${fmtDate(u.created_at)}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Connection Error', e.message);
  }
}

// ── Run Full Cycle ──
async function runFullCycle() {
  if (!requireUser()) return;
  const btn = $('btnCycle');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Running...';

  // Animate pipeline steps
  const steps = ['ps-analyze','ps-sanitize','ps-negotiate','ps-switch','ps-audit'];
  steps.forEach(s => { $(s).className = 'pipeline-step'; });

  try {
    // Animate steps one by one
    for (let i = 0; i < steps.length; i++) {
      $(steps[i]).className = 'pipeline-step active';
      await new Promise(r => setTimeout(r, 300));
    }

    const res = await apiPost(`/run-cycle/${selectedUser}`);
    const d = res.data;
    const status = d.final_status;

    steps.forEach(s => { $(s).className = 'pipeline-step done'; });
    if (d.errors) {
      steps.forEach(s => {
        if (!d.analysis && s === 'ps-analyze') $(s).className = 'pipeline-step error';
        if (!d.negotiation && s === 'ps-negotiate') $(s).className = 'pipeline-step error';
        if (!d.switching && s === 'ps-switch') $(s).className = 'pipeline-step error';
      });
    }

    const statusBadge = status === 'completed' ? badge('Completed', 'success') :
                        status === 'partial'   ? badge('Partial', 'warning') :
                                                 badge('Failed', 'danger');

    $('cycleResult').innerHTML = `
      <div class="result-grid" style="margin-top:20px">
        ${d.analysis ? `
        <div class="result-card">
          <div class="result-title">📊 Analysis</div>
          <div class="result-value">${(d.analysis.efficiency * 100).toFixed(1)}%</div>
          <div class="result-sub">Efficiency · ${d.analysis.recommendation?.toUpperCase() || '—'}</div>
          <div class="efficiency-bar-wrap"><div class="efficiency-bar"><div class="bar-fill ${effClass(d.analysis.efficiency)}" style="width:${(d.analysis.efficiency * 100)}%"></div></div></div>
        </div>` : ''}
        ${d.negotiation ? `
        <div class="result-card">
          <div class="result-title">🤝 Negotiation</div>
          <div class="result-value">₹${d.negotiation.final_price}</div>
          <div class="result-sub">${d.negotiation.savings_pct}% savings · ${d.negotiation.total_rounds} rounds · ${d.negotiation.status}</div>
        </div>` : ''}
        ${d.switching ? `
        <div class="result-card">
          <div class="result-title">🔄 Plan Switch</div>
          <div class="result-value">${d.switching.applied ? '✅ Applied' : '⏹ Skipped'}</div>
          <div class="result-sub">Risk: ${d.switching.risk_flag} · ₹${d.switching.projected_cost}</div>
        </div>` : ''}
        <div class="result-card">
          <div class="result-title">📋 Pipeline Status</div>
          <div class="result-value">${statusBadge}</div>
          <div class="result-sub">${(d.audit_logged || []).length} audit entries logged</div>
        </div>
      </div>
      ${d.errors ? `<div class="result-message" style="border-color:var(--warning);background:var(--warning-bg)"><span class="msg-icon">⚠️</span>${d.errors.join('<br>')}</div>` : ''}
    `;

    toast('success', 'Pipeline Complete', `Full cycle finished with status: ${status}`);
  } catch (e) {
    steps.forEach(s => { $(s).className = 'pipeline-step error'; });
    $('cycleResult').innerHTML = `<div class="result-message" style="border-color:var(--danger);background:var(--danger-bg)"><span class="msg-icon">❌</span>${esc(e.message)}</div>`;
    toast('error', 'Pipeline Failed', e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `🚀 Run Full Cycle for User ${selectedUser}`;
  }
}

// ═════════════════════════════════════════════════════════
//  USERS PAGE
// ═════════════════════════════════════════════════════════
async function renderUsers() {
  const body = $('contentBody');
  try {
    const res = await apiGet('/users');
    const users = res.data || [];

    body.innerHTML = `
      <div class="page-section">
        <div class="two-col">
          <!-- Users Table -->
          <div class="card">
            <div class="card-header"><h3>👤 All Users</h3>${badge(users.length + ' total', 'info')}</div>
            <div class="table-wrapper">
              <table>
                <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Phone</th><th>Joined</th></tr></thead>
                <tbody>
                  ${users.length === 0 ? '<tr><td colspan="5" class="table-empty"><div class="empty-icon">👤</div>No users yet</td></tr>' :
                    users.map(u => `
                    <tr>
                      <td><strong>${u.id}</strong></td>
                      <td>${esc(u.name)}</td>
                      <td style="color:var(--text-secondary)">${esc(u.email)}</td>
                      <td style="color:var(--text-muted)">${esc(u.phone || '—')}</td>
                      <td style="color:var(--text-muted)">${fmtDate(u.created_at)}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>

          <!-- Add User Form -->
          <div class="card">
            <div class="card-header"><h3>➕ Add New User</h3></div>
            <form onsubmit="addUser(event)">
              <div class="form-grid">
                <div class="form-group">
                  <label>Full Name</label>
                  <input type="text" id="newUserName" placeholder="e.g. Aarav Sharma" required />
                </div>
                <div class="form-group">
                  <label>Email</label>
                  <input type="email" id="newUserEmail" placeholder="e.g. aarav@example.com" required />
                </div>
                <div class="form-group">
                  <label>Phone (optional)</label>
                  <input type="text" id="newUserPhone" placeholder="+91-98765-43210" />
                </div>
              </div>
              <div class="form-actions" style="margin-top:16px">
                <button type="submit" class="btn btn-primary" id="btnAddUser">➕ Create User</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Failed to load users', e.message);
  }
}

async function addUser(e) {
  e.preventDefault();
  const btn = $('btnAddUser');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Creating...';
  try {
    const body = {
      name:  $('newUserName').value.trim(),
      email: $('newUserEmail').value.trim(),
    };
    const phone = $('newUserPhone').value.trim();
    if (phone) body.phone = phone;
    await apiPost('/users', body);
    toast('success', 'User Created', `${body.name} has been registered.`);
    await loadUsers();
    renderUsers();
  } catch (err) {
    toast('error', 'Failed to create user', err.message);
    btn.disabled = false;
    btn.innerHTML = '➕ Create User';
  }
}

// ═════════════════════════════════════════════════════════
//  SUBSCRIPTIONS PAGE
// ═════════════════════════════════════════════════════════
async function renderSubscriptions() {
  const body = $('contentBody');
  if (!requireUser()) {
    body.innerHTML = emptyState('📱', 'Select a User', 'Choose a user from the dropdown to view subscriptions.');
    return;
  }
  try {
    const res = await apiGet(`/subscriptions/user/${selectedUser}`);
    const subs = res.data || [];

    body.innerHTML = `
      <div class="page-section">
        <div class="two-col">
          <div class="card">
            <div class="card-header"><h3>📱 Subscriptions for User ${selectedUser}</h3>${badge(subs.length + ' plans', 'info')}</div>
            <div class="table-wrapper">
              <table>
                <thead><tr><th>ID</th><th>Provider</th><th>Plan</th><th>Cost</th><th>Data</th><th>Calls</th><th>Status</th></tr></thead>
                <tbody>
                  ${subs.length === 0 ? '<tr><td colspan="7" class="table-empty"><div class="empty-icon">📱</div>No subscriptions</td></tr>' :
                    subs.map(s => `
                    <tr>
                      <td><strong>${s.id}</strong></td>
                      <td>${esc(s.provider)}</td>
                      <td>${esc(s.plan_name)}</td>
                      <td style="color:var(--accent-primary);font-weight:600">₹${s.monthly_cost}</td>
                      <td>${s.data_limit_gb ? s.data_limit_gb + ' GB' : '∞'}</td>
                      <td>${s.call_minutes_limit ?? '∞'} min</td>
                      <td>${s.is_active ? badge('Active', 'success') : badge('Inactive', 'danger')}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h3>➕ Add Subscription</h3></div>
            <form onsubmit="addSubscription(event)">
              <div class="form-grid">
                <div class="form-group">
                  <label>Provider</label>
                  <select id="newSubProvider" required>
                    <option value="Jio">Jio</option>
                    <option value="Airtel">Airtel</option>
                    <option value="Vi">Vi</option>
                    <option value="BSNL">BSNL</option>
                    <option value="Netflix">Netflix</option>
                    <option value="Spotify">Spotify</option>
                    <option value="AWS">AWS</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>Plan Name</label>
                  <input type="text" id="newSubPlan" placeholder="e.g. Gold 599" required />
                </div>
                <div class="form-group">
                  <label>Monthly Cost (₹)</label>
                  <input type="number" id="newSubCost" step="0.01" min="1" placeholder="599" required />
                </div>
                <div class="form-group">
                  <label>Data Limit (GB)</label>
                  <input type="number" id="newSubData" step="0.1" min="0" placeholder="100" />
                </div>
                <div class="form-group">
                  <label>Call Minutes</label>
                  <input type="number" id="newSubCalls" min="0" placeholder="500" />
                </div>
              </div>
              <div class="form-actions" style="margin-top:16px">
                <button type="submit" class="btn btn-primary" id="btnAddSub">➕ Create Subscription</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Error', e.message);
  }
}

async function addSubscription(e) {
  e.preventDefault();
  if (!requireUser()) return;
  const btn = $('btnAddSub');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Creating...';
  try {
    const payload = {
      user_id: selectedUser,
      provider: $('newSubProvider').value,
      plan_name: $('newSubPlan').value.trim(),
      monthly_cost: parseFloat($('newSubCost').value),
    };
    const dataLimit = $('newSubData').value;
    const callLimit = $('newSubCalls').value;
    if (dataLimit) payload.data_limit_gb = parseFloat(dataLimit);
    if (callLimit) payload.call_minutes_limit = parseInt(callLimit);

    await apiPost('/subscriptions', payload);
    toast('success', 'Subscription Created', `${payload.provider} — ${payload.plan_name}`);
    renderSubscriptions();
  } catch (err) {
    toast('error', 'Failed', err.message);
    btn.disabled = false;
    btn.innerHTML = '➕ Create Subscription';
  }
}

// ═════════════════════════════════════════════════════════
//  USAGE PAGE
// ═════════════════════════════════════════════════════════
async function renderUsage() {
  const body = $('contentBody');
  if (!requireUser()) {
    body.innerHTML = emptyState('📈', 'Select a User', 'Choose a user to view usage data.');
    return;
  }
  try {
    const res = await apiGet(`/usage/user/${selectedUser}`);
    const records = res.data || [];

    // Simple bar chart data
    const chartData = records.slice(0, 12).reverse();
    const maxData = Math.max(...chartData.map(r => r.data_used_gb || 0), 1);

    body.innerHTML = `
      <div class="page-section">
        <!-- Usage Chart -->
        ${chartData.length > 0 ? `
        <div class="card" style="margin-bottom:20px">
          <div class="card-header"><h3>📊 Usage Trend (Data GB)</h3>${badge(records.length + ' records', 'info')}</div>
          <div class="chart-container">
            <div class="chart-bars">
              ${chartData.map(r => {
                const pct = ((r.data_used_gb || 0) / maxData * 100);
                const color = pct > 80 ? 'var(--danger)' : pct > 50 ? 'var(--warning)' : 'var(--accent-secondary)';
                return `<div class="chart-bar" style="height:${Math.max(pct, 3)}%;background:${color}">
                  <div class="bar-tooltip">${r.data_used_gb} GB · ₹${r.billing_amount}</div>
                </div>`;
              }).join('')}
            </div>
            <div class="chart-labels">
              ${chartData.map(r => `<span>${fmtDate(r.period_start).split(' ').slice(0,2).join(' ')}</span>`).join('')}
            </div>
          </div>
        </div>` : ''}

        <div class="two-col">
          <div class="card">
            <div class="card-header"><h3>📋 Usage Records</h3></div>
            <div class="table-wrapper">
              <table>
                <thead><tr><th>Provider</th><th>Period</th><th>Data</th><th>Calls</th><th>Billed</th></tr></thead>
                <tbody>
                  ${records.length === 0 ? '<tr><td colspan="5" class="table-empty"><div class="empty-icon">📈</div>No usage data</td></tr>' :
                    records.slice(0, 20).map(r => `
                    <tr>
                      <td>${esc(r.provider)}</td>
                      <td style="color:var(--text-muted);font-size:0.8rem">${fmtDate(r.period_start)} — ${fmtDate(r.period_end)}</td>
                      <td style="color:var(--accent-secondary);font-weight:600">${r.data_used_gb} GB</td>
                      <td>${r.call_minutes_used} min</td>
                      <td style="color:var(--accent-primary);font-weight:600">₹${r.billing_amount}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h3>➕ Add Usage Record</h3></div>
            <form onsubmit="addUsage(event)">
              <div class="form-grid">
                <div class="form-group">
                  <label>Provider</label>
                  <input type="text" id="usageProvider" placeholder="Jio" required />
                </div>
                <div class="form-group">
                  <label>Period Start</label>
                  <input type="datetime-local" id="usageStart" required />
                </div>
                <div class="form-group">
                  <label>Period End</label>
                  <input type="datetime-local" id="usageEnd" required />
                </div>
                <div class="form-group">
                  <label>Data Used (GB)</label>
                  <input type="number" id="usageData" step="0.01" min="0" value="0" />
                </div>
                <div class="form-group">
                  <label>Call Minutes</label>
                  <input type="number" id="usageCalls" min="0" value="0" />
                </div>
                <div class="form-group">
                  <label>Billing Amount (₹)</label>
                  <input type="number" id="usageBill" step="0.01" min="0" value="0" />
                </div>
              </div>
              <div class="form-actions" style="margin-top:16px">
                <button type="submit" class="btn btn-primary" id="btnAddUsage">➕ Add Record</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Error', e.message);
  }
}

async function addUsage(e) {
  e.preventDefault();
  if (!requireUser()) return;
  const btn = $('btnAddUsage');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Adding...';
  try {
    await apiPost('/usage', {
      user_id: selectedUser,
      provider: $('usageProvider').value.trim(),
      period_start: new Date($('usageStart').value).toISOString(),
      period_end:   new Date($('usageEnd').value).toISOString(),
      data_used_gb:      parseFloat($('usageData').value) || 0,
      call_minutes_used: parseInt($('usageCalls').value) || 0,
      billing_amount:    parseFloat($('usageBill').value) || 0,
    });
    toast('success', 'Usage Record Added');
    renderUsage();
  } catch (err) {
    toast('error', 'Failed', err.message);
    btn.disabled = false;
    btn.innerHTML = '➕ Add Record';
  }
}

// ═════════════════════════════════════════════════════════
//  ANALYSIS PAGE
// ═════════════════════════════════════════════════════════
async function renderAnalysis() {
  const body = $('contentBody');
  body.innerHTML = `
    <div class="page-section">
      <div class="card">
        <div class="card-header">
          <h3>🔍 Usage Analysis</h3>
          <button class="btn btn-primary" onclick="runAnalysis()" id="btnAnalyze" ${!selectedUser ? 'disabled' : ''}>
            📊 Analyze User ${selectedUser || '—'}
          </button>
        </div>
        <p style="color:var(--text-secondary);font-size:0.88rem;margin-bottom:16px">
          Evaluates subscription efficiency, calculates savings potential, and recommends plan changes.
        </p>
        <div id="analysisResult">
          ${!selectedUser ? emptyState('📊', 'Select a User', 'Choose a user to run analysis on.') : emptyState('📊', 'Ready to Analyze', 'Click the button above to run usage analysis.')}
        </div>
      </div>
    </div>
  `;
}

async function runAnalysis() {
  if (!requireUser()) return;
  const btn = $('btnAnalyze');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Analyzing...';
  $('analysisResult').innerHTML = loading('Analyzing usage patterns...');

  try {
    const res = await apiPost(`/analyze/${selectedUser}`);
    const d = res.data;

    $('analysisResult').innerHTML = `
      <div class="result-grid">
        <div class="result-card">
          <div class="result-title">Efficiency Score</div>
          <div class="result-value">${(d.efficiency * 100).toFixed(1)}%</div>
          <div class="efficiency-bar-wrap"><div class="efficiency-bar"><div class="bar-fill ${effClass(d.efficiency)}" style="width:${d.efficiency * 100}%"></div></div></div>
          <div class="result-sub">Usage Category: ${d.usage_category || '—'}</div>
        </div>
        <div class="result-card">
          <div class="result-title">Recommendation</div>
          <div class="result-value" style="font-size:1.4rem">${(d.recommendation || '—').toUpperCase()}</div>
          <div class="result-sub">Confidence: ${((d.confidence_score || 0) * 100).toFixed(0)}%</div>
        </div>
        <div class="result-card">
          <div class="result-title">Savings Estimate</div>
          <div class="result-value" style="color:var(--success)">₹${d.savings_estimate || 0}</div>
          <div class="result-sub">Per month potential saving</div>
        </div>
        <div class="result-card">
          <div class="result-title">Current Plan</div>
          <div class="result-value" style="font-size:1.2rem">${esc(d.provider || '—')} — ${esc(d.plan_name || '—')}</div>
          <div class="result-sub">₹${d.monthly_cost || 0}/month</div>
        </div>
      </div>
      <div class="result-grid" style="margin-top:16px">
        <div class="result-card">
          <div class="result-title">Avg Data Usage</div>
          <div class="result-value">${d.avg_data_usage || 0} GB</div>
        </div>
        <div class="result-card">
          <div class="result-title">Avg Call Usage</div>
          <div class="result-value">${d.avg_call_usage || 0} min</div>
        </div>
      </div>
      ${d.message ? `<div class="result-message"><span class="msg-icon">💬</span>${esc(d.message)}</div>` : ''}
    `;
    toast('success', 'Analysis Complete', `Recommendation: ${d.recommendation}`);
  } catch (e) {
    $('analysisResult').innerHTML = `<div class="result-message" style="border-color:var(--danger);background:var(--danger-bg)"><span class="msg-icon">❌</span>${esc(e.message)}</div>`;
    toast('error', 'Analysis Failed', e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `📊 Analyze User ${selectedUser}`;
  }
}

// ═════════════════════════════════════════════════════════
//  NEGOTIATION PAGE
// ═════════════════════════════════════════════════════════
async function renderNegotiation() {
  const body = $('contentBody');
  body.innerHTML = `
    <div class="page-section">
      <div class="card">
        <div class="card-header">
          <h3>🤝 Autonomous Negotiation</h3>
          <button class="btn btn-primary" onclick="runNegotiation()" id="btnNegotiate" ${!selectedUser ? 'disabled' : ''}>
            🤝 Negotiate for User ${selectedUser || '—'}
          </button>
        </div>
        <p style="color:var(--text-secondary);font-size:0.88rem;margin-bottom:16px">
          Simulates multi-round price negotiation between the user's digital twin and the service provider.
        </p>
        <div id="negotiationResult">
          ${!selectedUser ? emptyState('🤝', 'Select a User', 'Choose a user to run negotiation for.') : emptyState('🤝', 'Ready to Negotiate', 'Click the button above to start negotiation.')}
        </div>
      </div>
    </div>
  `;
}

async function runNegotiation() {
  if (!requireUser()) return;
  const btn = $('btnNegotiate');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Negotiating...';
  $('negotiationResult').innerHTML = loading('Running multi-round negotiation...');

  try {
    const res = await apiPost(`/negotiate/${selectedUser}`);
    const d = res.data;
    const rounds = d.rounds || [];

    const outcomeBadge = d.status === 'accepted' ? badge('Accepted', 'success') :
                         d.status === 'rejected' ? badge('Rejected', 'danger') : badge(d.status, 'warning');

    $('negotiationResult').innerHTML = `
      <div class="result-grid">
        <div class="result-card">
          <div class="result-title">Original Cost</div>
          <div class="result-value">₹${d.original_cost}</div>
          <div class="result-sub">${esc(d.provider)} — ${esc(d.plan_name)}</div>
        </div>
        <div class="result-card">
          <div class="result-title">Final Price</div>
          <div class="result-value" style="color:var(--success)">₹${d.final_price}</div>
          <div class="result-sub">${d.savings_pct}% savings achieved</div>
        </div>
        <div class="result-card">
          <div class="result-title">Total Rounds</div>
          <div class="result-value">${d.total_rounds}</div>
          <div class="result-sub">Offer-counteroffer cycles</div>
        </div>
        <div class="result-card">
          <div class="result-title">Outcome</div>
          <div class="result-value">${outcomeBadge}</div>
          <div class="result-sub">Efficiency used: ${((d.efficiency_used || 0) * 100).toFixed(1)}%</div>
        </div>
      </div>

      <!-- Round-by-round -->
      <div class="card" style="margin-top:20px">
        <div class="card-header"><h3>📜 Round-by-Round Breakdown</h3></div>
        <div class="rounds-timeline">
          ${rounds.map(r => `
            <div class="round-item">
              <div class="round-num">${r.round_number}</div>
              <div class="round-details">
                <div class="round-label">Round ${r.round_number}</div>
                <div class="round-values">
                  <span>Agent: <strong style="color:var(--accent-secondary)">₹${r.agent_offer}</strong></span>
                  <span>Provider: <strong style="color:var(--warning)">₹${r.provider_counter || '—'}</strong></span>
                </div>
              </div>
              <span class="round-status ${r.status}">${r.status}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
    toast('success', 'Negotiation Complete', `${d.savings_pct}% savings — ${d.status}`);
  } catch (e) {
    $('negotiationResult').innerHTML = `<div class="result-message" style="border-color:var(--danger);background:var(--danger-bg)"><span class="msg-icon">❌</span>${esc(e.message)}</div>`;
    toast('error', 'Negotiation Failed', e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `🤝 Negotiate for User ${selectedUser}`;
  }
}

// ═════════════════════════════════════════════════════════
//  PLAN SWITCHING PAGE
// ═════════════════════════════════════════════════════════
async function renderSwitching() {
  const body = $('contentBody');
  body.innerHTML = `
    <div class="page-section">
      <div class="card">
        <div class="card-header">
          <h3>🔄 Plan Switching</h3>
          <button class="btn btn-primary" onclick="runSwitch()" id="btnSwitch" ${!selectedUser ? 'disabled' : ''}>
            🔄 Switch Plan for User ${selectedUser || '—'}
          </button>
        </div>
        <p style="color:var(--text-secondary);font-size:0.88rem;margin-bottom:16px">
          Validates KPIs (cost reduction, SLA risk) and applies plan changes atomically with rollback support.
        </p>
        <div id="switchResult">
          ${!selectedUser ? emptyState('🔄', 'Select a User', 'Choose a user to attempt plan switch.') : emptyState('🔄', 'Ready', 'Run a negotiation first, then click switch.')}
        </div>
      </div>
    </div>
  `;
}

async function runSwitch() {
  if (!requireUser()) return;
  const btn = $('btnSwitch');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Switching...';
  $('switchResult').innerHTML = loading('Evaluating KPIs and applying plan change...');

  try {
    const res = await apiPost(`/switch/${selectedUser}`);
    const d = res.data;

    const riskColor = d.risk_flag === 'low' ? 'var(--success)' :
                      d.risk_flag === 'medium' ? 'var(--warning)' : 'var(--danger)';

    $('switchResult').innerHTML = `
      <div class="result-grid">
        <div class="result-card">
          <div class="result-title">Decision</div>
          <div class="result-value">${d.applied ? '✅ Applied' : '⏹ Rejected'}</div>
          <div class="result-sub">${esc(d.provider)} — ${esc(d.plan_name)}</div>
        </div>
        <div class="result-card">
          <div class="result-title">Projected Cost</div>
          <div class="result-value" style="color:var(--success)">₹${d.projected_cost}</div>
          <div class="result-sub">After switch</div>
        </div>
        <div class="result-card">
          <div class="result-title">Risk Level</div>
          <div class="result-value" style="color:${riskColor}">${(d.risk_flag || '—').toUpperCase()}</div>
          <div class="result-sub">SLA risk assessment</div>
        </div>
        <div class="result-card">
          <div class="result-title">Rollback</div>
          <div class="result-value">${d.rollback ? '🔙 Yes' : '✅ No'}</div>
          <div class="result-sub">${d.rollback ? 'Changes were rolled back' : 'No rollback needed'}</div>
        </div>
      </div>
      ${d.reason ? `<div class="result-message"><span class="msg-icon">💬</span>${esc(d.reason)}</div>` : ''}
    `;
    toast(d.applied ? 'success' : 'info', 'Plan Switch Complete', d.applied ? 'Plan successfully changed!' : 'Switch was rejected.');
  } catch (e) {
    $('switchResult').innerHTML = `<div class="result-message" style="border-color:var(--danger);background:var(--danger-bg)"><span class="msg-icon">❌</span>${esc(e.message)}</div>`;
    toast('error', 'Switch Failed', e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `🔄 Switch Plan for User ${selectedUser}`;
  }
}

// ═════════════════════════════════════════════════════════
//  AUDIT LOGS PAGE
// ═════════════════════════════════════════════════════════
async function renderAudit() {
  const body = $('contentBody');
  if (!requireUser()) {
    body.innerHTML = emptyState('📝', 'Select a User', 'Choose a user to view audit logs.');
    return;
  }
  $('contentBody').innerHTML = loading('Loading audit trail...');

  try {
    const res = await apiGet(`/audit/${selectedUser}`);
    const logs = res.data || [];

    const actionIcons = {
      analysis: '📊', negotiation: '🤝', switching: '🔄',
      switch: '🔄', switch_rejected: '⏹',
    };
    const actionColors = {
      analysis: 'info', negotiation: 'success', switching: 'warning',
      switch: 'success', switch_rejected: 'danger',
    };

    body.innerHTML = `
      <div class="page-section">
        <div class="card">
          <div class="card-header">
            <h3>📝 Audit Trail — User ${selectedUser}</h3>
            ${badge(logs.length + ' entries', 'info')}
          </div>
          ${logs.length === 0 ?
            emptyState('📝', 'No Audit Logs', 'Run the pipeline to generate explainable audit entries.') :
            `<div class="audit-timeline">
              ${logs.map(log => `
                <div class="audit-entry">
                  <div class="audit-header">
                    <span class="audit-action badge-${actionColors[log.action] || 'info'}">
                      ${actionIcons[log.action] || '📎'} ${esc(log.action)}
                    </span>
                    <span style="font-size:0.72rem;color:var(--text-muted)">Module: ${esc(log.module)}</span>
                    <span class="audit-time">${fmtDateTime(log.created_at)}</span>
                  </div>
                  <div class="audit-desc">${esc(log.description)}</div>
                </div>
              `).join('')}
            </div>`
          }
        </div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Error', e.message);
  }
}

// ═════════════════════════════════════════════════════════
//  INIT
// ═════════════════════════════════════════════════════════
(async function init() {
  await checkServer();
  await loadUsers();
  renderPage('dashboard');
  // Recheck server every 30s
  setInterval(checkServer, 30000);
})();
