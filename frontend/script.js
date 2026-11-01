/* ═══════════════════════════════════════════════════════════
   Agentic Digital Twin — Dashboard Application
   ═══════════════════════════════════════════════════════════ */

// Same origin as the dashboard (served by the API itself).
const API = '/api';
const TOKEN_KEY = 'twin.accessToken';

// ── State ────────────────────────────────────────────────
let currentPage  = 'dashboard';
let selectedUser = null;
let usersCache   = [];
let currentUser  = null;

// ── Session token (per browser tab; storage may be unavailable) ──
function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* storage blocked: the session lasts until reload */ }
  memoryToken = token;
}
let memoryToken = null;
function isAdmin() { return currentUser?.role === 'admin'; }

// ═════════════════════════════════════════════════════════
//  API HELPERS
// ═════════════════════════════════════════════════════════
async function api(path, method = 'GET', body = null) {
  const headers = { 'Content-Type': 'application/json' };
  const token = getToken() || memoryToken;
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);

  const res  = await fetch(`${API}${path}`, opts);
  const json = await res.json();

  if (res.status === 401 && !path.startsWith('/auth/')) {
    setToken(null);
    showLogin('Your session has ended — please sign in again.');
  }
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

function pct(ratio) {
  return ratio == null ? '—' : `${(ratio * 100).toFixed(1)}%`;
}

function effClass(eff) {
  if (eff < 0.4)  return 'low';
  if (eff <= 0.8) return 'good';
  return 'high';
}

// Approve / reject controls for a proposed recommendation.
function decisionPanel(recId, text, explanation, readOnly = false) {
  const list = (items, icon) => (items || []).map(i => `<div>${icon} ${esc(i)}</div>`).join('');
  return `
    <div class="result-message" id="decision-${recId}" style="border-color:var(--warning);background:var(--warning-bg)">
      <span class="msg-icon">📝</span>
      <div style="flex:1">
        <div><strong>Recommendation #${recId} awaits your decision.</strong> ${esc(text)}</div>
        ${explanation ? `
        <div style="margin-top:8px"><strong>${esc(explanation.what)}</strong></div>
        <div style="margin-top:4px;color:var(--text-secondary)">${esc(explanation.why)}</div>
        <div style="margin-top:6px">${list(explanation.benefits, '✔')}${list(explanation.drawbacks, '⚠')}</div>` : ''}
        ${readOnly ? '' : `
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-primary btn-sm" onclick="decideRecommendation(${recId}, 'approve')">✅ Approve &amp; apply</button>
          <button class="btn btn-secondary btn-sm" onclick="decideRecommendation(${recId}, 'reject')">✖ Reject</button>
        </div>`}
      </div>
    </div>`;
}

async function decideRecommendation(recId, decision) {
  const panel = $(`decision-${recId}`);
  try {
    const res = await apiPost(`/recommendations/${recId}/${decision}`);
    const status = res.data.status;
    if (panel) panel.innerHTML = `<span class="msg-icon">${status === 'executed' ? '✅' : '✖'}</span>
      Recommendation #${recId} ${status === 'executed' ? 'approved and applied' : esc(status)}.`;
    toast('success', 'Decision recorded', `Recommendation #${recId}: ${status}`);
    if (currentPage === 'approvals') renderApprovals();
  } catch (e) {
    toast('error', 'Decision failed', e.message);
    if (currentPage === 'approvals') renderApprovals();   // e.g. it expired
  }
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
  approvals:     'Approvals',
  plans:         'Plans & Alternatives',
  privacy:       'Privacy Center',
  activity:      'Agent Activity',
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
// Admins may list every user; everyone else only sees themselves.
async function listUsers() {
  if (isAdmin()) return (await apiGet('/users')).data || [];
  return currentUser ? [currentUser] : [];
}

async function loadUsers() {
  try {
    usersCache = await listUsers();
    const sel = $('globalUserSelect');
    sel.innerHTML = '<option value="">— Select User —</option>' +
      usersCache.map(u => `<option value="${u.id}">${esc(u.name)} (ID: ${u.id})</option>`).join('');
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
    await fetch('/');
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
    approvals:     renderApprovals,
    plans:         renderPlans,
    privacy:       renderPrivacy,
    activity:      renderActivity,
  };

  (renderers[page] || renderDashboard)();
}

// ═════════════════════════════════════════════════════════
//  DASHBOARD PAGE
// ═════════════════════════════════════════════════════════
async function renderDashboard() {
  const body = $('contentBody');
  try {
    const users = await listUsers();
    const t = selectedUser ? await dashboardTotals(selectedUser) : null;

    body.innerHTML = `
      <div class="page-section">
        ${t ? `
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-icon blue">📱</div>
            <div class="stat-info">
              <div class="stat-label">Subscriptions</div>
              <div class="stat-value">${t.subscriptions}</div>
              <div class="stat-change up">${t.activeSubscriptions} active</div>
            </div>
          </div>
          <div class="stat-card" onclick="navigate('approvals')" style="cursor:pointer">
            <div class="stat-icon orange">📝</div>
            <div class="stat-info">
              <div class="stat-label">Awaiting Your Approval</div>
              <div class="stat-value">${t.pending}</div>
              <div class="stat-change ${t.pending ? 'down' : 'up'}">₹${t.pendingSavings}/month on offer</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon green">💰</div>
            <div class="stat-info">
              <div class="stat-label">Approved Savings</div>
              <div class="stat-value">₹${t.approvedSavings}</div>
              <div class="stat-change up">per month · ${t.executed} change(s) applied</div>
            </div>
          </div>
          <div class="stat-card" onclick="navigate('activity')" style="cursor:pointer">
            <div class="stat-icon cyan">⚡</div>
            <div class="stat-info">
              <div class="stat-label">Last Agent Run</div>
              <div class="stat-value" style="font-size:1.1rem">${t.lastRun ? esc(t.lastRun.status.replace('_', ' ')) : 'Never'}</div>
              <div class="stat-change ${t.failedSteps ? 'down' : 'up'}">${t.lastRun
                ? `${fmtDateTime(t.lastRun.started_at)} · ${t.failedSteps} failed step(s)`
                : 'Run the cycle below'}</div>
            </div>
          </div>
        </div>` : ''}

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
        if (!d.negotiation && !d.skipped?.negotiation && s === 'ps-negotiate') $(s).className = 'pipeline-step error';
        if (!d.switching && !d.skipped?.switching && s === 'ps-switch') $(s).className = 'pipeline-step error';
      });
    }

    const statusBadge = status === 'completed'         ? badge('Completed', 'success') :
                        status === 'awaiting_approval' ? badge('Awaiting approval', 'warning') :
                        status === 'partial'           ? badge('Partial', 'warning') :
                                                         badge('Failed', 'danger');

    $('cycleResult').innerHTML = `
      <div class="result-grid" style="margin-top:20px">
        ${d.analysis ? `
        <div class="result-card">
          <div class="result-title">📊 Analysis</div>
          <div class="result-value">${pct(d.analysis.efficiency)}</div>
          <div class="result-sub">Efficiency · ${d.analysis.recommendation?.toUpperCase() || '—'}</div>
          ${d.analysis.efficiency != null ? `<div class="efficiency-bar-wrap"><div class="efficiency-bar"><div class="bar-fill ${effClass(d.analysis.efficiency)}" style="width:${Math.min(d.analysis.efficiency * 100, 100)}%"></div></div></div>` : ''}
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
          <div class="result-value">${d.switching.proposed ? '📝 Proposed' : '⏹ Not proposed'}</div>
          <div class="result-sub">Risk: ${esc(d.switching.risk_flag)} · ₹${d.switching.projected_cost}</div>
        </div>` : ''}
        ${Object.entries(d.skipped || {}).map(([step, reason]) => `
        <div class="result-card">
          <div class="result-title">⏭ ${esc(step)}</div>
          <div class="result-value">Skipped</div>
          <div class="result-sub">${esc(reason)}</div>
        </div>`).join('')}
        <div class="result-card">
          <div class="result-title">📋 Pipeline Status</div>
          <div class="result-value">${statusBadge}</div>
          <div class="result-sub">${(d.audit_logged || []).length} audit entries logged</div>
        </div>
      </div>
      ${d.recommendation ? decisionPanel(d.recommendation.recommendation_id,
          `₹${d.recommendation.current_cost} → ₹${d.recommendation.projected_cost} ` +
          `(save ₹${d.recommendation.estimated_monthly_savings}/month).`, d.recommendation.explanation) : ''}
      ${d.errors ? `<div class="result-message" style="border-color:var(--warning);background:var(--warning-bg)"><span class="msg-icon">⚠️</span>${d.errors.map(esc).join('<br>')}</div>` : ''}
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
    const users = await listUsers();

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

          <!-- Add User Form (admins only; users sign up on the login screen) -->
          <div class="card" id="addUserCard">
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
    if (!isAdmin()) $('addUserCard')?.remove();
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
          <div class="result-title">Utilisation (${esc(d.binding_metric || 'n/a')})</div>
          <div class="result-value">${pct(d.efficiency)}</div>
          ${d.efficiency != null ? `<div class="efficiency-bar-wrap"><div class="efficiency-bar"><div class="bar-fill ${effClass(d.efficiency)}" style="width:${Math.min(d.efficiency * 100, 100)}%"></div></div></div>` : ''}
          <div class="result-sub">Data ${pct(d.data_utilization)} · Calls ${pct(d.call_utilization)} · ${esc(d.usage_category || '—')}</div>
        </div>
        <div class="result-card">
          <div class="result-title">Recommendation</div>
          <div class="result-value" style="font-size:1.4rem">${esc((d.recommendation || '—').toUpperCase())}</div>
          <div class="result-sub">Confidence: ${((d.confidence_score || 0) * 100).toFixed(0)}% · ${d.periods || 0} periods</div>
        </div>
        <div class="result-card">
          <div class="result-title">Unused Spend</div>
          <div class="result-value">₹${d.unused_spend || 0}</div>
          <div class="result-sub">Paying for capacity you don't use — not a saving</div>
        </div>
        <div class="result-card">
          <div class="result-title">Current Plan</div>
          <div class="result-value" style="font-size:1.2rem">${esc(d.provider || '—')} — ${esc(d.plan_name || '—')}</div>
          <div class="result-sub">₹${d.monthly_cost || 0}/month</div>
        </div>
      </div>
      <div class="result-grid" style="margin-top:16px">
        <div class="result-card">
          <div class="result-title">Data Usage</div>
          <div class="result-value">${d.avg_data_usage || 0} GB</div>
          <div class="result-sub">Peak ${d.peak_data_usage || 0} GB · trend ${esc(d.data_trend || '—')} · next month ~${d.projected_data_usage || 0} GB</div>
        </div>
        <div class="result-card">
          <div class="result-title">Call Usage</div>
          <div class="result-value">${d.avg_call_usage || 0} min</div>
          <div class="result-sub">Peak ${d.peak_call_usage || 0} min</div>
        </div>
        <div class="result-card">
          <div class="result-title">Overage Billed</div>
          <div class="result-value">₹${d.avg_overage || 0}</div>
          <div class="result-sub">Average per month above the plan price</div>
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
          <div class="result-sub">Efficiency used: ${pct(d.efficiency_used)} · walk-away ₹${d.reservation_price}${d.best_alternative ? ` (${esc(d.best_alternative.plan_name)})` : ''}</div>
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
            🔄 Evaluate Switch for User ${selectedUser || '—'}
          </button>
        </div>
        <p style="color:var(--text-secondary);font-size:0.88rem;margin-bottom:16px">
          Validates KPIs (cost reduction, added value, SLA risk) and proposes a change.
          Nothing is applied until you approve it.
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
  btn.innerHTML = '<span class="spinner"></span> Evaluating...';
  $('switchResult').innerHTML = loading('Evaluating KPIs...');

  try {
    const res = await apiPost(`/switch/${selectedUser}`);
    const d = res.data;

    const riskColor = d.risk_flag === 'low' ? 'var(--success)' :
                      d.risk_flag === 'medium' ? 'var(--warning)' : 'var(--danger)';

    $('switchResult').innerHTML = `
      <div class="result-grid">
        <div class="result-card">
          <div class="result-title">Decision</div>
          <div class="result-value">${d.proposed ? '📝 Proposed' : '⏹ Not proposed'}</div>
          <div class="result-sub">${esc(d.provider)} — ${esc(d.plan_name)}</div>
        </div>
        <div class="result-card">
          <div class="result-title">Projected Cost</div>
          <div class="result-value" style="color:var(--success)">₹${d.projected_cost}</div>
          <div class="result-sub">Currently ₹${d.current_cost}</div>
        </div>
        <div class="result-card">
          <div class="result-title">Risk Level</div>
          <div class="result-value" style="color:${riskColor}">${esc((d.risk_flag || '—').toUpperCase())}</div>
          <div class="result-sub">SLA risk assessment</div>
        </div>
      </div>
      ${d.reason ? `<div class="result-message"><span class="msg-icon">💬</span>${esc(d.reason)}</div>` : ''}
      ${d.proposed ? decisionPanel(d.recommendation_id, `Save ₹${d.estimated_monthly_savings}/month if approved.`, d.explanation) : ''}
    `;
    toast('info', 'Evaluation Complete', d.proposed ? 'Change proposed — awaiting your approval.' : 'No change proposed.');
  } catch (e) {
    $('switchResult').innerHTML = `<div class="result-message" style="border-color:var(--danger);background:var(--danger-bg)"><span class="msg-icon">❌</span>${esc(e.message)}</div>`;
    toast('error', 'Switch Failed', e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `🔄 Evaluate Switch for User ${selectedUser}`;
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
// ═════════════════════════════════════════════════════════
//  SHARED HELPERS (Phase F pages)
// ═════════════════════════════════════════════════════════
async function apiPut(path, body) { return api(path, 'PUT', body); }

// Only the account owner may decide or change settings; admins view.
function canAct() { return currentUser && Number(selectedUser) === currentUser.id; }

function readOnlyNote() {
  return canAct() ? '' : `<div class="result-message"><span class="msg-icon">👁</span>
    View only — only the account owner can make decisions or change these settings.</div>`;
}

function statusBadge(status) {
  const map = {
    proposed: 'warning', executed: 'success', rejected: 'danger', expired: 'danger', failed: 'danger',
    completed: 'success', awaiting_approval: 'warning', partial: 'warning', running: 'info', skipped: 'info',
  };
  return badge(String(status).replace('_', ' '), map[status] || 'info');
}

function money(v) { return v == null ? '—' : `₹${Number(v).toFixed(2).replace(/\.00$/, '')}`; }
function limitText(v, unit) { return v == null ? 'unlimited' : `${v} ${unit}`; }

async function dashboardTotals(userId) {
  const [subs, recs, runs] = await Promise.all([
    apiGet(`/subscriptions/user/${userId}`).then(r => r.data || []).catch(() => []),
    apiGet(`/recommendations/user/${userId}?limit=200`).then(r => r.data || []).catch(() => []),
    apiGet(`/runs/user/${userId}?limit=1`).then(r => r.data || []).catch(() => []),
  ]);
  const pending = recs.filter(r => r.status === 'proposed');
  const executed = recs.filter(r => r.status === 'executed');
  const sum = list => list.reduce((acc, r) => acc + (r.estimated_monthly_savings || 0), 0).toFixed(2);
  const lastRun = runs[0] || null;
  return {
    subscriptions: subs.length,
    activeSubscriptions: subs.filter(s => s.is_active).length,
    pending: pending.length,
    pendingSavings: sum(pending),
    executed: executed.length,
    approvedSavings: sum(executed),
    lastRun,
    failedSteps: lastRun ? lastRun.steps.filter(s => s.status === 'failed').length : 0,
  };
}

// ═════════════════════════════════════════════════════════
//  APPROVALS PAGE
// ═════════════════════════════════════════════════════════
async function renderApprovals() {
  const body = $('contentBody');
  if (!selectedUser) { body.innerHTML = emptyState('📝', 'Select a User', 'Choose a user to see their recommendations.'); return; }
  try {
    const recs = (await apiGet(`/recommendations/user/${selectedUser}?limit=100`)).data || [];
    const open = recs.filter(r => r.status === 'proposed');
    const decided = recs.filter(r => r.status !== 'proposed');

    body.innerHTML = `
      <div class="page-section">
        ${readOnlyNote()}
        <div class="card">
          <div class="card-header"><h3>📝 Awaiting Your Decision</h3>${badge(open.length + ' open', open.length ? 'warning' : 'success')}</div>
          ${open.length === 0
            ? emptyState('✅', 'Nothing to decide', 'Run the decision cycle on the dashboard to look for savings.')
            : open.map(r => recommendationCard(r)).join('')}
        </div>
        <div class="card" style="margin-top:20px">
          <div class="card-header"><h3>🗂 Decision History</h3>${badge(decided.length + ' decided', 'info')}</div>
          <div class="table-wrapper"><table>
            <thead><tr><th>#</th><th>Action</th><th>Price</th><th>Saving / month</th><th>Status</th><th>Decided</th></tr></thead>
            <tbody>
              ${decided.length === 0 ? '<tr><td colspan="6" class="table-empty">No decisions yet</td></tr>' :
                decided.map(r => `<tr>
                  <td>${r.id}</td>
                  <td>${esc(r.action.replace(/_/g, ' '))}</td>
                  <td>${money(r.current_cost)} → ${money(r.proposed_cost)}</td>
                  <td>${money(r.estimated_monthly_savings)}</td>
                  <td>${statusBadge(r.status)}</td>
                  <td style="color:var(--text-muted)">${r.decided_at ? fmtDateTime(r.decided_at) : '—'}</td>
                </tr>`).join('')}
            </tbody>
          </table></div>
        </div>
      </div>`;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Failed to load recommendations', e.message);
  }
}

function recommendationCard(r) {
  const ex = r.evidence?.explanation;
  const options = (r.evidence?.options_considered || [])
    .map(o => `${esc(o.plan_name)} (${esc(o.action.replace(/_/g, ' '))}): ${money(o.monthly_savings)}/month`).join(' · ');
  const excluded = (r.evidence?.alternatives_excluded || [])
    .map(a => `<div>✖ ${esc(a.plan_name)} — ${esc(a.reasons[0] || '')}</div>`).join('');
  return `
    ${decisionPanel(r.id, `${money(r.current_cost)} → ${money(r.proposed_cost)} (save ${money(r.estimated_monthly_savings)}/month).`, ex, !canAct())}
    <details style="margin:4px 0 16px 0;color:var(--text-secondary);font-size:0.82rem">
      <summary style="cursor:pointer">Evidence behind recommendation #${r.id}</summary>
      <div style="margin-top:8px">
        <div><strong>Options compared:</strong> ${options || '—'}</div>
        <div><strong>Risk:</strong> ${esc(r.evidence?.risk_flag || '—')} · <strong>minimum saving:</strong> ${r.evidence?.minimum_savings_pct ?? '—'}%</div>
        ${ex?.data_used ? `<div><strong>Usage used:</strong> ${Object.entries(ex.data_used)
            .filter(([, v]) => v !== null && v !== undefined)
            .map(([k, v]) => `${esc(k.replace(/_/g, ' '))} ${esc(String(v))}`).join(' · ')}</div>` : ''}
        ${excluded ? `<div style="margin-top:6px"><strong>Plans ruled out:</strong>${excluded}</div>` : ''}
        <div style="margin-top:6px;color:var(--text-muted)">Proposed ${fmtDateTime(r.created_at)} · run #${r.run_id ?? '—'}</div>
      </div>
    </details>`;
}

// ═════════════════════════════════════════════════════════
//  PLANS & ALTERNATIVES PAGE
// ═════════════════════════════════════════════════════════
async function renderPlans() {
  const body = $('contentBody');
  if (!selectedUser) { body.innerHTML = emptyState('📦', 'Select a User', 'Choose a user to compare plans.'); return; }
  try {
    const subs = ((await apiGet(`/subscriptions/user/${selectedUser}`)).data || []).filter(s => s.is_active);
    body.innerHTML = `
      <div class="page-section">
        ${readOnlyNote()}
        <p style="color:var(--text-secondary);font-size:0.88rem;margin-bottom:16px">
          Catalog plans are checked against your peak and projected usage (+10% headroom) and your
          preferences. Catalog prices are demo data, not live provider pricing.
        </p>
        ${subs.length === 0 ? emptyState('📦', 'No active subscriptions', 'Add one on the Subscriptions page.') :
          subs.map(s => `
          <div class="card" style="margin-bottom:20px">
            <div class="card-header">
              <h3>📱 ${esc(s.provider)} — ${esc(s.plan_name)}</h3>
              ${badge(money(s.monthly_cost) + '/month', 'info')}
            </div>
            <div style="color:var(--text-secondary);font-size:0.85rem;margin-bottom:12px">
              Data ${limitText(s.data_limit_gb, 'GB')} · Calls ${limitText(s.call_minutes_limit, 'min')}
            </div>
            <div id="alts-${s.id}">${loading('Comparing catalog plans...')}</div>
            <div id="history-${s.id}" style="margin-top:16px"></div>
          </div>`).join('')}
      </div>`;
    for (const s of subs) {
      loadAlternatives(s.id);
      loadHistory(s.id);
    }
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Failed to load plans', e.message);
  }
}

async function loadAlternatives(subId) {
  const box = $(`alts-${subId}`);
  try {
    const a = (await apiGet(`/subscriptions/${subId}/alternatives`)).data;
    const p = a.usage_profile || {};
    const row = (plan, extra) => `<tr>
      <td><strong>${esc(plan.provider)} ${esc(plan.plan_name)}</strong></td>
      <td>${money(plan.monthly_cost)}</td>
      <td>${limitText(plan.data_limit_gb, 'GB')}</td>
      <td>${limitText(plan.call_minutes_limit, 'min')}</td>
      <td>${extra}</td></tr>`;
    box.innerHTML = `
      <div style="font-size:0.82rem;color:var(--text-secondary);margin-bottom:10px">
        Needs at least <strong>${a.required.data_gb} GB</strong> and <strong>${a.required.call_minutes} min</strong>
        (peak ${p.peak_data_usage ?? 0} GB / ${p.peak_call_usage ?? 0} min, trend ${esc(p.data_trend || '—')}).
        Effective current cost ${money(a.effective_current_cost)}/month${p.avg_overage ? ` incl. ${money(p.avg_overage)} overage` : ''}.
      </div>
      <div class="table-wrapper"><table>
        <thead><tr><th>Plan</th><th>Price</th><th>Data</th><th>Calls</th><th>Verdict</th></tr></thead>
        <tbody>
          ${a.fits.map(f => row(f, `${badge('fits', 'success')} saves ${money(f.monthly_savings)} (${f.savings_pct}%)`)).join('')}
          ${a.excluded.map(x => row(x, `${badge('ruled out', 'danger')} <span style="color:var(--text-muted)">${esc(x.reasons.join('; '))}</span>`)).join('')}
          ${a.fits.length + a.excluded.length === 0 ? '<tr><td colspan="5" class="table-empty">No comparable catalog plans</td></tr>' : ''}
        </tbody>
      </table></div>`;
  } catch (e) {
    box.innerHTML = `<div class="result-message"><span class="msg-icon">🔒</span>${esc(e.message)}</div>`;
  }
}

async function loadHistory(subId) {
  const box = $(`history-${subId}`);
  try {
    const hist = (await apiGet(`/subscriptions/${subId}/history`)).data || [];
    if (hist.length === 0) { box.innerHTML = ''; return; }
    const last = hist[hist.length - 1];
    box.innerHTML = `
      <div style="font-weight:600;margin-bottom:8px">🕑 Change history</div>
      <div class="table-wrapper"><table>
        <thead><tr><th>When</th><th>Change</th><th>Price</th><th>Reason</th></tr></thead>
        <tbody>${hist.map(h => `<tr>
          <td style="color:var(--text-muted)">${fmtDateTime(h.created_at)}</td>
          <td>${esc(h.change_type.replace('_', ' '))}</td>
          <td>${money(h.previous_cost)} → ${money(h.new_cost)}</td>
          <td style="color:var(--text-secondary)">${esc(h.reason || '')}</td></tr>`).join('')}
        </tbody>
      </table></div>
      ${canAct() && last.change_type !== 'rollback'
        ? `<button class="btn btn-secondary btn-sm" style="margin-top:10px" onclick="rollbackSubscription(${subId})">↩ Undo last change</button>`
        : ''}`;
  } catch (e) {
    box.innerHTML = `<div class="result-message"><span class="msg-icon">⚠️</span>${esc(e.message)}</div>`;
  }
}

async function rollbackSubscription(subId) {
  if (!confirm('Restore the plan terms from before the last change?')) return;
  try {
    const r = (await apiPost(`/subscriptions/${subId}/rollback`)).data;
    toast('success', 'Change undone', `Price restored to ${money(r.restored_cost)}/month.`);
    renderPlans();
  } catch (e) {
    toast('error', 'Rollback failed', e.message);
  }
}

// ═════════════════════════════════════════════════════════
//  PRIVACY CENTER
// ═════════════════════════════════════════════════════════
async function renderPrivacy() {
  const body = $('contentBody');
  if (!selectedUser) { body.innerHTML = emptyState('🔒', 'Select a User', 'Choose a user to manage privacy.'); return; }
  try {
    const [consents, prefs, logs] = await Promise.all([
      apiGet(`/users/${selectedUser}/consents`).then(r => r.data),
      apiGet(`/users/${selectedUser}/preferences`).then(r => r.data),
      apiGet(`/audit/${selectedUser}?limit=200`).then(r => r.data || []),
    ]);
    const events = logs.filter(l => l.action === 'consent' || l.action === 'access_denied').slice(0, 15);
    const disabled = canAct() ? '' : 'disabled';

    body.innerHTML = `
      <div class="page-section">
        ${readOnlyNote()}
        <div class="two-col">
          <div class="card">
            <div class="card-header"><h3>🔒 Permissions</h3>${badge(consents.filter(c => c.granted).length + '/' + consents.length + ' granted', 'info')}</div>
            <p style="color:var(--text-secondary);font-size:0.82rem;margin-bottom:12px">
              Every agent asks the Privacy Mediator before using your data. Without a permission, that step is skipped.
            </p>
            ${consents.map(c => `
              <label class="consent-row">
                <input type="checkbox" ${c.granted ? 'checked' : ''} ${disabled}
                       onchange="toggleConsent('${esc(c.scope)}', this)" />
                <span>
                  <strong>${esc(c.scope.replace(/_/g, ' '))}</strong>
                  <span class="consent-desc">${esc(c.description)}</span>
                </span>
              </label>`).join('')}
          </div>
          <div class="card">
            <div class="card-header"><h3>🎯 Preferences</h3></div>
            <p style="color:var(--text-secondary);font-size:0.82rem;margin-bottom:12px">
              The twin only recommends catalog plans that meet these.
            </p>
            <form onsubmit="savePreferences(event)">
              <div class="form-grid">
                <div class="form-group"><label>Max budget (₹/month)</label>
                  <input type="number" min="1" step="any" id="prefBudget" value="${prefs.max_monthly_budget ?? ''}" ${disabled} /></div>
                <div class="form-group"><label>Min data (GB)</label>
                  <input type="number" min="0" step="any" id="prefData" value="${prefs.min_data_gb ?? ''}" ${disabled} /></div>
                <div class="form-group"><label>Min call minutes</label>
                  <input type="number" min="0" step="1" id="prefCalls" value="${prefs.min_call_minutes ?? ''}" ${disabled} /></div>
                <div class="form-group"><label>Required features (comma-separated)</label>
                  <input type="text" id="prefFeatures" placeholder="5g, hotspot" value="${esc(prefs.required_features.join(', '))}" ${disabled} /></div>
                <div class="form-group"><label>Preferred providers (comma-separated)</label>
                  <input type="text" id="prefProviders" placeholder="Jio, Airtel" value="${esc(prefs.preferred_providers.join(', '))}" ${disabled} /></div>
                <label class="consent-row" style="align-self:end">
                  <input type="checkbox" id="prefAllowChange" ${prefs.allow_provider_change ? 'checked' : ''} ${disabled} />
                  <span><strong>Allow switching provider</strong></span>
                </label>
              </div>
              ${canAct() ? '<button class="btn btn-primary" type="submit" id="btnPrefs" style="margin-top:12px">💾 Save preferences</button>' : ''}
            </form>
          </div>
        </div>
        <div class="card" style="margin-top:20px">
          <div class="card-header"><h3>🕑 Privacy Activity</h3>${badge(events.length + ' recent', 'info')}</div>
          ${events.length === 0 ? emptyState('🔒', 'No privacy events yet', 'Permission changes and blocked agent access appear here.') :
            `<div class="audit-timeline">${events.map(l => `
              <div class="audit-entry">
                <div class="audit-header">
                  <span class="audit-action badge-${l.action === 'access_denied' ? 'danger' : 'info'}">${l.action === 'access_denied' ? '⛔ access denied' : '🔑 consent'}</span>
                  <span class="audit-time">${fmtDateTime(l.created_at)}</span>
                </div>
                <div class="audit-desc">${esc(l.description)}</div>
              </div>`).join('')}</div>`}
        </div>
      </div>`;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Failed to load privacy settings', e.message);
  }
}

async function toggleConsent(scope, checkbox) {
  checkbox.disabled = true;
  try {
    await apiPut(`/users/${selectedUser}/consents/${scope}`, { granted: checkbox.checked });
    toast('success', 'Permission updated', `${scope.replace(/_/g, ' ')}: ${checkbox.checked ? 'granted' : 'revoked'}`);
    renderPrivacy();
  } catch (e) {
    checkbox.checked = !checkbox.checked;
    checkbox.disabled = false;
    toast('error', 'Update failed', e.message);
  }
}

async function savePreferences(e) {
  e.preventDefault();
  const num = id => { const v = $(id).value.trim(); return v === '' ? null : Number(v); };
  const list = id => $(id).value.split(',').map(x => x.trim()).filter(Boolean);
  const btn = $('btnPrefs');
  btn.disabled = true;
  try {
    await apiPut(`/users/${selectedUser}/preferences`, {
      max_monthly_budget: num('prefBudget'),
      min_data_gb: num('prefData'),
      min_call_minutes: num('prefCalls'),
      required_features: list('prefFeatures'),
      preferred_providers: list('prefProviders'),
      allow_provider_change: $('prefAllowChange').checked,
    });
    toast('success', 'Preferences saved', 'Future recommendations will respect them.');
    renderPrivacy();
  } catch (err) {
    toast('error', 'Save failed', err.message);
    btn.disabled = false;
  }
}

// ═════════════════════════════════════════════════════════
//  AGENT ACTIVITY PAGE
// ═════════════════════════════════════════════════════════
const AGENT_LABELS = {
  usage_analyzer: '📊 Usage Analyzer',
  privacy_mediator: '🔒 Privacy Mediator',
  negotiation_agent: '🤝 Negotiation Agent',
  plan_switching: '🔄 Plan Switching',
};

function summaryText(summary) {
  if (!summary) return '';
  return Object.entries(summary)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join(' · ');
}

async function renderActivity() {
  const body = $('contentBody');
  if (!selectedUser) { body.innerHTML = emptyState('⚡', 'Select a User', 'Choose a user to see agent activity.'); return; }
  try {
    const runs = (await apiGet(`/runs/user/${selectedUser}?limit=20`)).data || [];

    // Per-agent status, from recorded steps only
    const health = Object.keys(AGENT_LABELS).map(agent => {
      const steps = runs.flatMap(r => r.steps.filter(s => s.agent === agent).map(s => ({ ...s, run: r })));
      const last = steps[0];
      const count = st => steps.filter(s => s.status === st).length;
      const avg = steps.filter(s => s.duration_ms != null);
      return { agent, last, completed: count('completed'), skipped: count('skipped'), failed: count('failed'),
               avgMs: avg.length ? Math.round(avg.reduce((a, s) => a + s.duration_ms, 0) / avg.length) : null };
    });

    body.innerHTML = `
      <div class="page-section">
        <div class="card">
          <div class="card-header"><h3>🤖 Agents</h3>${badge('last ' + runs.length + ' runs', 'info')}</div>
          <div class="table-wrapper"><table>
            <thead><tr><th>Agent</th><th>Last status</th><th>Completed</th><th>Skipped</th><th>Failed</th><th>Avg time</th><th>Last note</th></tr></thead>
            <tbody>${health.map(h => `<tr>
              <td><strong>${AGENT_LABELS[h.agent]}</strong></td>
              <td>${h.last ? statusBadge(h.last.status) : badge('not run yet', 'info')}</td>
              <td>${h.completed}</td><td>${h.skipped}</td>
              <td style="color:${h.failed ? 'var(--danger)' : 'inherit'}">${h.failed}</td>
              <td>${h.avgMs == null ? '—' : h.avgMs + ' ms'}</td>
              <td style="color:var(--text-secondary);font-size:0.8rem">${esc(h.last?.error || summaryText(h.last?.summary) || '')}</td>
            </tr>`).join('')}</tbody>
          </table></div>
        </div>
        <div class="card" style="margin-top:20px">
          <div class="card-header"><h3>⚡ Run History</h3></div>
          ${runs.length === 0 ? emptyState('⚡', 'No runs yet', 'Run the decision cycle from the dashboard.') :
            runs.map(r => `
            <details class="run-entry">
              <summary>
                <strong>Run #${r.id}</strong> ${statusBadge(r.status)}
                <span style="color:var(--text-muted);font-size:0.8rem">${fmtDateTime(r.started_at)}</span>
                ${r.error ? `<span style="color:var(--danger);font-size:0.8rem">${esc(r.error)}</span>` : ''}
              </summary>
              <div class="table-wrapper"><table>
                <thead><tr><th>Step</th><th>Status</th><th>Time</th><th>Details</th></tr></thead>
                <tbody>${r.steps.map(s => `<tr>
                  <td>${AGENT_LABELS[s.agent] || esc(s.agent)}</td>
                  <td>${statusBadge(s.status)}</td>
                  <td>${s.duration_ms == null ? '—' : s.duration_ms + ' ms'}</td>
                  <td style="color:${s.error ? 'var(--danger)' : 'var(--text-secondary)'};font-size:0.8rem">${esc(s.error || summaryText(s.summary))}</td>
                </tr>`).join('')}</tbody>
              </table></div>
            </details>`).join('')}
        </div>
      </div>`;
  } catch (e) {
    body.innerHTML = emptyState('⚠️', 'Failed to load agent activity', e.message);
  }
}

let registering = false;

function showLogin(message = '') {
  $('loginOverlay').hidden = false;
  $('loginError').textContent = message;
  $('btnLogout').hidden = true;
  $('sessionUser').textContent = '';
}

function toggleRegister() {
  registering = !registering;
  $('loginNameGroup').hidden = !registering;
  $('loginName').required = registering;
  $('loginTitle').textContent = registering ? 'Create your account' : 'Sign in to your digital twin';
  $('btnLogin').textContent = registering ? 'Create account' : 'Sign in';
  $('loginPassword').autocomplete = registering ? 'new-password' : 'current-password';
  $('loginSwitch').innerHTML = registering
    ? 'Have an account? <a onclick="toggleRegister()">Sign in</a>'
    : 'No account? <a onclick="toggleRegister()">Create one</a>';
  $('loginError').textContent = '';
}

async function submitLogin(e) {
  e.preventDefault();
  const btn = $('btnLogin');
  btn.disabled = true;
  try {
    const body = { email: $('loginEmail').value.trim(), password: $('loginPassword').value };
    if (registering) body.name = $('loginName').value.trim();
    const res = await apiPost(registering ? '/auth/register' : '/auth/login', body);
    setToken(res.data.access_token);
    $('loginPassword').value = '';
    await startSession();
  } catch (err) {
    $('loginError').textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

async function startSession() {
  currentUser = (await apiGet('/auth/me')).data;
  $('loginOverlay').hidden = true;
  $('btnLogout').hidden = false;
  $('sessionUser').textContent = `${currentUser.name}${isAdmin() ? ' (admin — read only for others)' : ''}`;
  await loadUsers();
  renderPage('dashboard');
}

function logout() {
  setToken(null);
  currentUser = null;
  usersCache = [];
  selectedUser = null;
  $('contentBody').innerHTML = '';
  showLogin();
}

(async function init() {
  await checkServer();
  if (getToken()) {
    try { await startSession(); } catch { showLogin(); }
  } else {
    showLogin();
  }
  // Recheck server every 30s
  setInterval(checkServer, 30000);
})();
