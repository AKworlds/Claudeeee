// Personal dashboard SPA. Vanilla ES module, no build step.
const $ = (sel, root = document) => root.querySelector(sel);
const view = $('#view');

// ---------- helpers ----------
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
const money = (v, d = false) => (v == null || isNaN(v) ? '–' : (d ? usd2 : usd).format(v));
const pct = (v, digits = 1) => (v == null || isNaN(v) ? '–' : (v * 100).toFixed(digits) + '%');
const num = (v, d = 1) => (v == null || isNaN(v) ? '–' : Number(v).toFixed(d));
const signed = (v) => (v == null || isNaN(v) ? '–' : (v >= 0 ? '+' : '−') + usd.format(Math.abs(v)));
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);

/** Tiny DOM builder: h('div', {class:'x', onclick}, child, 'text'). Text is always a text node. */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function toast(msg, kind = 'info') {
  const t = h('div', { class: 'toast ' + kind, role: kind === 'error' ? 'alert' : null }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 7000 : 3500);
}

class ApiError extends Error {
  constructor(msg, status, errors) { super(msg); this.status = status; this.errors = errors || []; }
}

async function api(path, { method = 'GET', body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch('/api' + path, opts); }
  catch (e) { throw new ApiError('Network error: ' + e.message, 0); }
  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  if (!res.ok) {
    let msg = res.statusText || 'Request failed', errors = [];
    const d = data && typeof data === 'object' ? data.detail : null;
    if (typeof d === 'string') msg = d;
    else if (Array.isArray(d)) {
      errors = d.map((x) => ({ field: (x.loc || []).filter((p) => p !== 'body').join('.'), msg: x.msg }));
      msg = errors.map((e) => (e.field ? e.field + ': ' : '') + e.msg).join('; ');
    } else if (typeof data === 'string' && data) msg = data.slice(0, 200);
    throw new ApiError(msg, res.status, errors);
  }
  return data;
}
/** GET that toasts on failure and returns null. */
async function get(path) {
  try { return await api(path); }
  catch (e) { toast(`${path}: ${e.message}`, 'error'); return null; }
}

// ---------- charts ----------
let charts = [], pending = [];
function destroyCharts() { charts.forEach((c) => c.destroy()); charts = []; }
function mountCharts() { const todo = pending; pending = []; todo.forEach((fn) => fn()); }
const palette = () => [cssVar('--c1'), cssVar('--c2'), cssVar('--c3'), cssVar('--c4'), cssVar('--c5'), cssVar('--c6')];

function chartCard(title, build, { tall = false, note } = {}) {
  const canvas = h('canvas', { role: 'img', 'aria-label': title });
  const card = h('section', { class: 'card' }, h('h2', null, title), h('div', { class: 'chart-box' + (tall ? ' tall' : '') }, canvas), note && h('div', { class: 'legend-note' }, note));
  pending.push(() => {
    if (!window.Chart) { card.querySelector('.chart-box').replaceChildren(h('div', { class: 'empty' }, 'Chart library failed to load (offline?).')); return; }
    const cfg = build();
    const text = cssVar('--muted'), grid = cssVar('--grid');
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false }, cfg.options);
    const o = cfg.options;
    o.plugins = Object.assign({ legend: { labels: { color: text, boxWidth: 12 } } }, o.plugins);
    o.interaction = o.interaction || { mode: 'index', intersect: false };
    const sc = o.scales || { x: {}, y: {} };
    for (const k of Object.keys(sc)) {
      sc[k].ticks = Object.assign({ color: text, maxRotation: 0, autoSkipPadding: 12 }, sc[k].ticks);
      sc[k].grid = Object.assign({ color: grid }, sc[k].grid);
      if (sc[k].title) sc[k].title.color = text;
    }
    o.scales = sc;
    charts.push(new Chart(canvas, cfg));
  });
  return card;
}
const moneyTick = { callback: (v) => (Math.abs(v) >= 1000 ? '$' + v / 1000 + 'k' : '$' + v) };
const moneyTooltip = { callbacks: { label: (c) => `${c.dataset.label}: ${usd.format(c.parsed.y ?? c.parsed.x)}` } };
const monthLabel = (m) => { const [y, mo] = String(m).split('-'); return mo ? new Date(+y, +mo - 1, 1).toLocaleString('en-US', { month: 'short', year: '2-digit' }) : m; };
const TYPE_ORDER = ['paycheck', 'dividend', 'interest', 'business', 'other'];

function emptyState(msg) { return h('div', { class: 'empty card' }, msg); }
const needImport = () => emptyState('Upload your budget workbook in Settings to see this.');
function tile(label, value, sub, cls) {
  return h('div', { class: 'tile' }, h('div', { class: 'label' }, label), h('div', { class: 'value ' + (cls || '') }, value), sub != null && h('div', { class: 'sub' }, sub));
}

// ---------- forms / dialog ----------
const dlg = $('#dlg'), dlgForm = $('#dlg-form');

function fieldEl(f, val) {
  let input;
  const id = 'f_' + f.key;
  if (f.type === 'select') input = h('select', { id, name: f.key }, f.options.map((o) => h('option', { value: o, selected: o === val }, o)));
  else if (f.type === 'textarea') input = h('textarea', { id, name: f.key, rows: 3 }, val ?? '');
  else if (f.type === 'checkbox') return h('label', { for: id }, h('input', { id, name: f.key, type: 'checkbox', checked: !!val }), f.label);
  else input = h('input', { id, name: f.key, type: f.type || 'text', value: val ?? '', step: f.type === 'number' ? 'any' : null, required: f.required, readonly: f.readonly });
  return h('label', { for: id }, f.label + (f.required ? ' *' : ''), input, h('span', { class: 'field-error', id: 'err_' + f.key }));
}

function readForm(form, fields) {
  const out = {};
  for (const f of fields) {
    const el = form.elements[f.key];
    if (!el || f.readonly) continue;
    if (f.type === 'checkbox') out[f.key] = el.checked;
    else if (f.type === 'number') out[f.key] = el.value === '' ? null : Number(el.value);
    else out[f.key] = el.value.trim() === '' ? (f.required ? '' : null) : el.value.trim();
  }
  return out;
}

/** Open the shared <dialog> with a form. onSave(values) must return a promise; errors are shown inline. */
function openForm({ title, fields, values = {}, onSave }) {
  const err = h('div', { class: 'errors', hidden: true, role: 'alert' });
  const save = h('button', { type: 'submit', class: 'primary' }, 'Save');
  dlgForm.replaceChildren(
    h('h2', null, title), err,
    h('div', { class: 'form-grid' }, fields.map((f) => fieldEl(f, values[f.key]))),
    h('div', { class: 'dlg-actions' }, h('button', { type: 'button', onclick: () => dlg.close() }, 'Cancel'), save));
  dlgForm.onsubmit = async (ev) => {
    ev.preventDefault();
    save.disabled = true; err.hidden = true;
    dlgForm.querySelectorAll('.field-error').forEach((e) => (e.textContent = ''));
    try { await onSave(readForm(dlgForm, fields)); dlg.close(); }
    catch (e) {
      err.hidden = false; err.textContent = e.message;
      for (const x of e.errors || []) { const t = $('#err_' + x.field.split('.').pop(), dlgForm); if (t) t.textContent = x.msg; }
    } finally { save.disabled = false; }
  };
  dlg.showModal();
  dlgForm.querySelector('input,select,textarea')?.focus();
}

const confirmDelete = (what) => window.confirm(`Delete ${what}? This cannot be undone.`);

// ---------- header ----------
async function refreshHeader() {
  try {
    const s = await api('/finance/summary');
    const li = s && s.last_import;
    const when = li && typeof li === 'object' ? li.imported_at : li;
    $('#last-import').textContent = when ? 'Last import: ' + new Date(when).toLocaleString() : 'No import yet';
    return s;
  } catch { $('#last-import').textContent = 'No import yet'; return null; }
}

// ---------- views ----------
async function viewOverview() {
  const [s, nw, inc, car] = await Promise.all([get('/finance/summary'), get('/finance/net-worth?months=36'), get('/finance/income?months=12'), get('/career/summary')]);
  const frag = h('div');
  const hasFinance = s && s.last_import;
  if (!hasFinance) frag.append(h('div', { class: 'banner' }, 'Upload your budget workbook in Settings to see your finance numbers.'));
  const chg = s?.net_worth_change_30d;
  const fiPct = s?.fi_progress;
  frag.append(h('div', { class: 'grid tiles' },
    tile('Net worth', money(s?.net_worth), chg != null ? signed(chg) + ' in 30 days' : null),
    tile('Monthly income', money(s?.monthly_income)),
    tile('Savings rate', pct(s?.savings_rate), s?.monthly_spend != null ? 'Spend ' + money(s.monthly_spend) + '/mo' : null),
    tile('Emergency fund', s?.emergency_months != null ? num(s.emergency_months) + ' mo' : '–'),
    tile('FI progress', pct(fiPct), s?.fi_number ? 'of ' + money(s.fi_number) : null),
    tile('Business profit YTD', money(s?.business_profit_ytd)),
    tile('Active applications', car?.active ?? '–', car ? `${car.applications_total} total` : null),
    tile('Interviews', car?.interviews ?? '–', car?.offers != null ? `${car.offers} offers` : null)));
  frag.append(h('div', { class: 'grid cols-2' }, netWorthLine(nw), incomeStack(inc)));
  return frag;
}

function netWorthLine(nw) {
  const pts = nw?.points || [];
  if (!pts.length) return emptyState('No net worth data yet. Upload your budget workbook in Settings to see this.');
  return chartCard('Net worth', () => ({
    type: 'line',
    data: { labels: pts.map((p) => monthLabel(p.month)), datasets: [{ label: 'Net worth', data: pts.map((p) => p.net_worth), borderColor: cssVar('--c1'), backgroundColor: cssVar('--c1'), tension: .25, pointRadius: 2 }] },
    options: { plugins: { legend: { display: false }, tooltip: moneyTooltip }, scales: { x: {}, y: { ticks: moneyTick } } },
  }));
}

function incomeStack(inc) {
  const months = inc?.months || [];
  if (!months.length) return emptyState('No income data yet. Upload your budget workbook in Settings to see this.');
  const keys = [...TYPE_ORDER.filter((k) => inc.series?.[k]), ...Object.keys(inc.series || {}).filter((k) => !TYPE_ORDER.includes(k))];
  return chartCard('Income by type (12 months)', () => {
    const pal = palette();
    return {
      type: 'bar',
      data: { labels: months.map(monthLabel), datasets: keys.map((k, i) => ({ label: k[0].toUpperCase() + k.slice(1), data: inc.series[k], backgroundColor: pal[i % pal.length], stack: 'income' })) },
      options: { plugins: { tooltip: moneyTooltip }, scales: { x: { stacked: true }, y: { stacked: true, ticks: moneyTick } } },
    };
  });
}

let ruleMonth = '';
async function viewMoney() {
  const q = ruleMonth ? '?month=' + encodeURIComponent(ruleMonth) : '';
  const [inc, cash, rule, nw, biz] = await Promise.all([get('/finance/income?months=12'), get('/finance/cashflow?months=12'), get('/finance/budget-rule' + q), get('/finance/net-worth?months=36'), get('/finance/business?months=12')]);
  const frag = h('div');
  if (!(inc?.months?.length || cash?.points?.length)) frag.append(h('div', { class: 'banner' }, 'Upload your budget workbook in Settings to see this.'));
  frag.append(h('div', { class: 'grid cols-2' }, incomeStack(inc), cashflowChart(cash)));
  frag.append(budgetRulePanel(rule));
  frag.append(h('div', { class: 'grid cols-2' }, assetsChart(nw), businessChart(biz)));
  return frag;
}

function cashflowChart(cash) {
  const pts = cash?.points || [];
  if (!pts.length) return emptyState('No cashflow data yet.');
  return chartCard('Cashflow: income vs spending', () => {
    const [c1, c2, c3, c4] = palette();
    return {
      type: 'bar',
      data: { labels: pts.map((p) => monthLabel(p.month)), datasets: [
        { type: 'line', label: 'Income', data: pts.map((p) => p.income), borderColor: cssVar('--text'), backgroundColor: cssVar('--text'), pointRadius: 3, order: 0 },
        { label: 'Needs', data: pts.map((p) => p.needs), backgroundColor: c1, stack: 's' },
        { label: 'Wants', data: pts.map((p) => p.wants), backgroundColor: c3, stack: 's' },
        { label: 'Savings', data: pts.map((p) => p.savings), backgroundColor: c2, stack: 's' },
      ] },
      options: { plugins: { tooltip: moneyTooltip }, scales: { x: { stacked: true }, y: { stacked: true, ticks: moneyTick } } },
    };
  });
}

function budgetRulePanel(rule) {
  const monthInput = h('input', { type: 'month', class: 'inline-input', id: 'rule-month', value: ruleMonth || rule?.month || '', onchange: (e) => { ruleMonth = e.target.value; render(); } });
  const head = h('div', { class: 'row between' }, h('h2', null, '50/30/20 budget'), h('label', { for: 'rule-month', class: 'row', style: 'margin:0' }, 'Month', monthInput));
  const card = h('section', { class: 'card' }, head);
  if (!rule?.buckets?.length) { card.append(h('div', { class: 'empty' }, 'No budget data for this month.')); return card; }
  card.append(h('p', { class: 'muted small' }, `Net income ${money(rule.net_income)}. Marker shows target.`));
  const pal = { needs: cssVar('--c1'), wants: cssVar('--c3'), savings: cssVar('--c2') };
  for (const b of rule.buckets) {
    const max = Math.max(60, b.actual_pct, b.target_pct) * 1.1;
    const isSavings = b.bucket === 'savings';
    const bad = isSavings ? b.delta_pct < -0.5 : b.delta_pct > 0.5;
    const good = isSavings ? b.delta_pct >= 0 : b.delta_pct <= 0;
    const delta = (b.delta_pct >= 0 ? '+' : '−') + Math.abs(b.delta_pct).toFixed(1) + ' pts';
    card.append(h('div', { class: 'rule-row' },
      h('strong', null, b.bucket[0].toUpperCase() + b.bucket.slice(1)),
      h('div', null,
        h('div', { class: 'rule-bars', role: 'img', 'aria-label': `${b.bucket} actual ${b.actual_pct.toFixed(1)} percent, target ${b.target_pct} percent` },
          h('div', { class: 'actual', style: `width:${(b.actual_pct / max) * 100}%;background:${pal[b.bucket] || cssVar('--c6')}` }),
          h('div', { class: 'target', style: `left:${(b.target_pct / max) * 100}%` })),
        h('div', { class: 'small muted' }, `${money(b.actual)} · ${b.actual_pct.toFixed(1)}% of ${b.target_pct}% target`)),
      h('span', { class: bad ? 'bad' : good ? 'good' : '' }, delta)));
  }
  return card;
}

function assetsChart(nw) {
  const pts = nw?.points || [];
  if (!pts.length) return emptyState('No balance data yet.');
  return chartCard('Assets, liabilities and net worth', () => ({
    type: 'line',
    data: { labels: pts.map((p) => monthLabel(p.month)), datasets: [
      { label: 'Assets', data: pts.map((p) => p.assets), borderColor: cssVar('--c2'), backgroundColor: cssVar('--c2') + '33', fill: true, tension: .25, pointRadius: 0 },
      { label: 'Liabilities', data: pts.map((p) => p.liabilities), borderColor: cssVar('--c5'), backgroundColor: cssVar('--c5') + '33', fill: true, tension: .25, pointRadius: 0 },
      { label: 'Net worth', data: pts.map((p) => p.net_worth), borderColor: cssVar('--c1'), backgroundColor: cssVar('--c1') + '33', fill: true, tension: .25, pointRadius: 0 },
    ] },
    options: { plugins: { tooltip: moneyTooltip }, scales: { x: {}, y: { ticks: moneyTick } } },
  }));
}

function businessChart(biz) {
  const pts = biz?.points || [];
  if (!pts.length) return emptyState('No side business data (optional sheet).');
  return chartCard('Side business', () => ({
    type: 'bar',
    data: { labels: pts.map((p) => monthLabel(p.month)), datasets: [
      { label: 'Revenue', data: pts.map((p) => p.revenue), backgroundColor: cssVar('--c2') },
      { label: 'Expense', data: pts.map((p) => p.expense), backgroundColor: cssVar('--c3') },
      { type: 'line', label: 'Profit', data: pts.map((p) => p.profit), borderColor: cssVar('--c1'), backgroundColor: cssVar('--c1'), pointRadius: 3 },
    ] },
    options: { plugins: { tooltip: moneyTooltip }, scales: { x: {}, y: { ticks: moneyTick } } },
  }), { note: biz.ytd_profit != null ? 'Profit YTD: ' + money(biz.ytd_profit) : null });
}

// Milestones
const MS_FIELDS = [
  { key: 'name', label: 'Name', required: true },
  { key: 'kind', label: 'Kind', type: 'select', options: ['emergency_fund', 'net_worth', 'investing', 'fi', 'custom'] },
  { key: 'metric', label: 'Metric', type: 'select', options: ['net_worth', 'emergency_months', 'invested', 'custom'] },
  { key: 'target', label: 'Target', type: 'number', required: true },
];
const fmtMetric = (m, v) => (m === 'emergency_months' ? num(v) + ' mo' : m === 'custom' ? num(v, 0) : money(v));

async function viewMilestones() {
  const data = await get('/finance/milestones');
  const list = data?.milestones || [];
  const frag = h('div');
  const card = h('section', { class: 'card' },
    h('div', { class: 'row between' }, h('h2', null, 'Milestones'),
      h('button', { class: 'primary', onclick: () => openForm({ title: 'Add milestone', fields: MS_FIELDS, values: { kind: 'custom', metric: 'net_worth' }, onSave: async (v) => { await api('/finance/milestones', { method: 'POST', body: v }); toast('Milestone added'); render(); } }) }, 'Add milestone')));
  if (!list.length) card.append(h('div', { class: 'empty' }, 'No milestones yet. Add one, or upload your workbook so defaults can be calculated.'));
  for (const m of list) {
    const p = Math.max(0, Math.min(1, m.progress || 0));
    card.append(h('div', { class: 'ms' },
      h('div', { class: 'ms-head' },
        h('div', null, h('strong', null, m.name), ' ', m.achieved_at ? h('span', { class: 'badge good' }, 'Achieved ' + String(m.achieved_at).slice(0, 10)) : h('span', { class: 'badge' }, m.kind)),
        h('div', { class: 'row' },
          h('span', { class: 'muted' }, `${fmtMetric(m.metric, m.current)} / ${fmtMetric(m.metric, m.target)} (${pct(m.progress, 0)})`),
          h('button', { class: 'small', onclick: () => openForm({ title: 'Edit milestone', fields: MS_FIELDS, values: m, onSave: async (v) => { await api('/finance/milestones/' + m.id, { method: 'PUT', body: v }); render(); } }) }, 'Edit'),
          h('button', { class: 'small danger', onclick: async () => { if (!confirmDelete(`milestone "${m.name}"`)) return; try { await api('/finance/milestones/' + m.id, { method: 'DELETE' }); render(); } catch (e) { toast(e.message, 'error'); } } }, 'Delete'))),
      h('div', { class: 'progress' + (p >= 1 ? ' done' : ''), role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(p * 100), 'aria-label': m.name }, h('span', { style: `width:${p * 100}%` }))));
  }
  frag.append(card);
  return frag;
}

async function viewForecast() {
  const f = await get('/finance/forecast');
  const frag = h('div');
  const sc = f?.scenarios || [];
  if (!sc.length) return needImport();
  frag.append(h('div', { class: 'grid tiles' }, tile('Starting net worth', money(f.start_net_worth)), tile('Monthly contribution', money(f.monthly_contribution))));
  frag.append(chartCard('Net worth forecast (10 years)', () => {
    const pal = [cssVar('--c6'), cssVar('--c1'), cssVar('--c2')];
    const n = Math.max(...sc.map((s) => s.series.length));
    const first = sc[0].series;
    return {
      type: 'line',
      data: { labels: Array.from({ length: n }, (_, i) => (first[i] ? first[i].month_index : i)), datasets: sc.map((s, i) => ({ label: `${s.name} (${pct(s.rate, 0)})`, data: s.series.map((p) => p.value), borderColor: pal[i % 3], backgroundColor: pal[i % 3], pointRadius: 0, tension: .2 })) },
      options: { plugins: { tooltip: { callbacks: { title: (it) => `Year ${(it[0].label / 12).toFixed(1)}`, label: (c) => `${c.dataset.label}: ${usd.format(c.parsed.y)}` } } },
        scales: { x: { ticks: { callback(v) { const m = +this.getLabelForValue(v); return m % 24 === 0 ? 'Yr ' + m / 12 : ''; }, autoSkip: false, maxRotation: 0 }, grid: { display: false } }, y: { ticks: moneyTick } } },
    };
  }, { tall: true }));
  const rows = sc.map((s) => h('tr', null, h('td', null, s.name), h('td', { class: 'num' }, pct(s.rate, 1)), h('td', { class: 'num' }, money(s.at?.['1y'])), h('td', { class: 'num' }, money(s.at?.['5y'])), h('td', { class: 'num' }, money(s.at?.['10y']))));
  frag.append(h('section', { class: 'card' }, h('h2', null, 'Projected net worth'),
    h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Scenario', 'Return', '1 year', '5 years', '10 years'].map((t, i) => h('th', { class: i ? 'num' : '' }, t)))), h('tbody', null, rows))),
    h('p', { class: 'legend-note' }, 'Monthly compounding plus your monthly contribution. Adjust rates in Settings.')));
  return frag;
}

// Career
const STATUS_CLS = { offer: 'good', earned: 'good', done: 'good', rejected: 'bad', expired: 'bad' };
const badge = (s) => h('span', { class: 'badge ' + (STATUS_CLS[s] || '') }, s || '–');
const linkOrDash = (u, text = 'link') => { const s = safeUrl(u); return s ? h('a', { href: s, target: '_blank', rel: 'noopener noreferrer' }, text) : '–'; };

const CAREER_TABLES = {
  applications: { title: 'Applications', fields: [
    { key: 'company', label: 'Company', required: true }, { key: 'role', label: 'Role', required: true },
    { key: 'status', label: 'Status', type: 'select', options: ['wishlist', 'applied', 'interview', 'offer', 'rejected', 'withdrawn'] },
    { key: 'applied_on', label: 'Applied on', type: 'date' }, { key: 'source', label: 'Source' }, { key: 'url', label: 'URL', type: 'url' },
    { key: 'notes', label: 'Notes', type: 'textarea' }],
    cols: [['Company', (r) => r.company], ['Role', (r) => r.role], ['Status', (r) => badge(r.status)], ['Applied', (r) => r.applied_on || '–'], ['Source', (r) => r.source || '–'], ['Link', (r) => linkOrDash(r.url)]] },
  certifications: { title: 'Certifications', fields: [
    { key: 'name', label: 'Name', required: true }, { key: 'issuer', label: 'Issuer' },
    { key: 'status', label: 'Status', type: 'select', options: ['planned', 'in_progress', 'earned', 'expired'] },
    { key: 'earned_on', label: 'Earned on', type: 'date' }, { key: 'expires_on', label: 'Expires on', type: 'date' },
    { key: 'target_date', label: 'Target date', type: 'date' }, { key: 'url', label: 'URL', type: 'url' }],
    cols: [['Name', (r) => r.name], ['Issuer', (r) => r.issuer || '–'], ['Status', (r) => badge(r.status)], ['Earned', (r) => r.earned_on || '–'], ['Expires', (r) => r.expires_on || '–'], ['Target', (r) => r.target_date || '–'], ['Link', (r) => linkOrDash(r.url)]] },
  projects: { title: 'Projects', fields: [
    { key: 'name', label: 'Name', required: true }, { key: 'description', label: 'Description', type: 'textarea' },
    { key: 'url', label: 'URL', type: 'url' }, { key: 'language', label: 'Language' },
    { key: 'status', label: 'Status', type: 'select', options: ['idea', 'building', 'done'] },
    { key: 'featured', label: 'Featured', type: 'checkbox' }],
    cols: [['Name', (r) => r.name], ['Description', (r) => r.description || '–', 'wrap'], ['Language', (r) => r.language || '–'], ['Stars', (r) => r.stars ?? 0], ['Status', (r) => badge(r.status)], ['Source', (r) => r.source || '–'], ['Featured', (r) => (r.featured ? 'Yes' : '')], ['Link', (r) => linkOrDash(r.url)]] },
};

function crudCard(kind, rows) {
  const cfg = CAREER_TABLES[kind];
  const defaults = { applications: { status: 'applied' }, certifications: { status: 'planned' }, projects: { status: 'idea' } }[kind];
  const form = (title, values, id) => openForm({ title, fields: cfg.fields, values, onSave: async (v) => { await api(`/career/${kind}${id ? '/' + id : ''}`, { method: id ? 'PUT' : 'POST', body: id ? { ...values, ...v } : v }); toast('Saved'); render(); } });
  const card = h('section', { class: 'card' }, h('div', { class: 'row between' }, h('h2', null, cfg.title), h('button', { class: 'primary', onclick: () => form('Add ' + cfg.title.toLowerCase().replace(/s$/, ''), defaults) }, 'Add')));
  if (!rows.length) { card.append(h('div', { class: 'empty' }, `No ${cfg.title.toLowerCase()} yet.`)); return card; }
  const head = h('tr', null, cfg.cols.map((c) => h('th', null, c[0])), h('th', null, h('span', { class: 'sr-only' }, 'Actions')));
  const body = rows.map((r) => h('tr', null, cfg.cols.map((c) => h('td', { class: c[2] || '' }, c[1](r))),
    h('td', { class: 'actions' },
      h('button', { class: 'small', onclick: () => form('Edit', r, r.id) }, 'Edit'),
      h('button', { class: 'small danger', onclick: async () => { if (!confirmDelete(`"${r.name || r.company}"`)) return; try { await api(`/career/${kind}/${r.id}`, { method: 'DELETE' }); render(); } catch (e) { toast(e.message, 'error'); } } }, 'Delete'))));
  card.append(h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, head), h('tbody', null, body))));
  return card;
}

function syncButton(label, path, confirmMsg) {
  const b = h('button', { onclick: async () => {
    if (confirmMsg && !confirm(confirmMsg)) return;
    b.disabled = true; const old = b.textContent; b.textContent = 'Syncing…';
    try { const r = await api(path, { method: 'POST' }); toast(`${label}: ${r.message || `${r.added} added, ${r.updated} updated`}`); render(); }
    catch (e) { toast(`${label} failed: ${e.message}`, 'error'); }
    finally { b.disabled = false; b.textContent = old; }
  } }, label);
  return b;
}

async function viewCareer() {
  const [sum, funnel, apps, certs, projs] = await Promise.all([get('/career/summary'), get('/career/applications/funnel'), get('/career/applications'), get('/career/certifications'), get('/career/projects')]);
  const frag = h('div');
  const ls = sum?.last_sync || {};
  const syncNote = (k) => (ls[k] ? 'Last ' + new Date(ls[k]).toLocaleString() : 'Never synced');
  frag.append(h('div', { class: 'card row between' },
    h('div', { class: 'row' }, syncButton('Sync GitHub', '/career/sync/github'), syncButton('Sync email', '/career/sync/email'),
      syncButton('Full email rescan', '/career/sync/email?full=true', 'Rescan your whole mailbox history (about 10 years)? This can take a few minutes.')),
    h('span', { class: 'muted small' }, `GitHub: ${syncNote('github')} · Email: ${syncNote('email')}`)));
  frag.append(h('div', { class: 'grid tiles' },
    tile('Applications', sum?.applications_total ?? '–', sum ? `${sum.active} active` : null),
    tile('Interviews', sum?.interviews ?? '–', sum ? `${sum.offers} offers` : null),
    tile('Response rate', pct(sum?.response_rate, 0)),
    tile('Certifications', sum?.certs_earned ?? '–', sum ? `${sum.certs_in_progress} in progress` : null),
    tile('Projects', sum?.projects_total ?? '–')));
  const stages = funnel?.stages || [], weekly = funnel?.weekly || [];
  frag.append(h('div', { class: 'grid cols-2' },
    stages.length ? chartCard('Application funnel', () => ({ type: 'bar',
      data: { labels: stages.map((s) => s.status), datasets: [{ label: 'Applications', data: stages.map((s) => s.count), backgroundColor: cssVar('--c1') }] },
      options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { ticks: { precision: 0 }, beginAtZero: true }, y: {} } } })) : emptyState('No applications yet. Add one below or sync your email.'),
    weekly.length ? chartCard('Applications per week', () => ({ type: 'bar',
      data: { labels: weekly.map((w) => w.week), datasets: [{ label: 'Applied', data: weekly.map((w) => w.applied), backgroundColor: cssVar('--c2') }] },
      options: { plugins: { legend: { display: false } }, scales: { x: {}, y: { ticks: { precision: 0 }, beginAtZero: true } } } })) : emptyState('No weekly data yet.')));
  frag.append(crudCard('applications', apps || []), crudCard('certifications', certs || []), crudCard('projects', projs || []));
  return frag;
}

// Settings
async function viewSettings() {
  const [a, imps] = await Promise.all([get('/finance/assumptions'), get('/finance/imports')]);
  const frag = h('div');
  // upload
  const fileIn = h('input', { type: 'file', id: 'wb-file', accept: '.xlsx' });
  const result = h('div', { class: 'small', role: 'status' });
  const upBtn = h('button', { class: 'primary', onclick: async () => {
    if (!fileIn.files[0]) { toast('Choose an .xlsx file first', 'error'); return; }
    const fd = new FormData(); fd.append('file', fileIn.files[0]);
    upBtn.disabled = true; result.textContent = 'Uploading…'; result.className = 'small';
    try {
      const r = await api('/finance/import', { method: 'POST', form: fd });
      if (r.status === 'error') throw new Error('Import failed: ' + r.message + ' (your previous data is unchanged)');
      result.className = 'small good'; result.textContent = 'Import finished: ' + (typeof r === 'object' ? (r.message || r.status || '') + ' ' + ['income', 'transactions', 'balances', 'business'].filter((k) => r['rows_' + k] != null).map((k) => `${k}: ${r['rows_' + k]}`).join(', ') : r);
      toast('Workbook imported'); refreshHeader(); render();
    } catch (e) { result.className = 'small bad'; result.textContent = e.message; toast(e.message, 'error'); }
    finally { upBtn.disabled = false; }
  } }, 'Upload');
  frag.append(h('section', { class: 'card' }, h('h2', null, 'Budget workbook'),
    h('p', { class: 'muted small' }, 'Upload an .xlsx (max 20 MB). Each import replaces your finance data with the workbook contents.'),
    h('label', { for: 'wb-file' }, 'Workbook file', fileIn), h('div', { class: 'row' }, upBtn), result));

  // google drive
  const drive = await get('/finance/drive');
  const driveRes = h('div', { class: 'small', role: 'status' });
  const driveBtn = h('button', { class: 'primary', disabled: !drive?.configured, onclick: async () => {
    driveBtn.disabled = true; driveRes.className = 'small'; driveRes.textContent = 'Syncing…';
    try {
      const r = await api('/finance/drive/sync', { method: 'POST' });
      if (r.status === 'error') throw new Error('Import failed: ' + r.message);
      driveRes.className = 'small good'; driveRes.textContent = r.status === 'skipped' ? 'Already up to date.' : 'Synced: ' + r.message;
      refreshHeader(); render();
    } catch (e) { driveRes.className = 'small bad'; driveRes.textContent = e.message; toast(e.message, 'error'); }
    finally { driveBtn.disabled = !drive?.configured; }
  } }, 'Sync now');
  frag.append(h('section', { class: 'card' }, h('h2', null, 'Google Drive workbook'),
    h('p', { class: 'muted small' }, drive?.configured
      ? ['Auto-sync is on. Share the workbook (Viewer) with: ', h('strong', null, drive.service_account_email || 'unknown (check the key file)')]
      : 'Not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE and workbook.drive_file_id (see the README) to sync from Google Drive.'),
    drive?.configured ? h('p', { class: 'small' }, 'Last sync: ' + (drive.last_sync ? new Date(drive.last_sync).toLocaleString() : 'never') + (drive.last_modified ? ' · File modified: ' + new Date(drive.last_modified).toLocaleString() : '')) : null,
    h('div', { class: 'row' }, driveBtn, h('a', { class: 'btn', style: 'text-decoration:none;display:inline-flex;align-items:center', href: '/api/finance/template', download: 'budget_template.xlsx' }, 'Download blank template')), driveRes));

  // assumptions
  const fields = [
    ['safe_withdrawal_rate', 'Safe withdrawal rate (0.04 = 4%)'], ['return_rates.conservative', 'Return: conservative'], ['return_rates.base', 'Return: base'], ['return_rates.optimistic', 'Return: optimistic'],
    ['monthly_contribution_override', 'Monthly contribution override (blank = auto)'], ['emergency_target_months', 'Emergency fund target (months)'],
    ['budget_targets.needs', 'Target: needs %'], ['budget_targets.wants', 'Target: wants %'], ['budget_targets.savings', 'Target: savings %'],
  ];
  const pick = (o, path) => path.split('.').reduce((x, k) => x?.[k], o);
  const form = h('form', { novalidate: true }, h('div', { class: 'form-grid' }, fields.map(([k, l]) => h('label', { for: 'as_' + k }, l, h('input', { id: 'as_' + k, name: k, type: 'number', step: 'any', value: pick(a, k) ?? '' }), h('span', { class: 'field-error', id: 'aserr_' + k })))));
  const aerr = h('div', { class: 'errors', hidden: true, role: 'alert' });
  const saveBtn = h('button', { type: 'submit', class: 'primary' }, 'Save assumptions');
  form.append(h('div', { class: 'row' }, saveBtn));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault(); aerr.hidden = true; form.querySelectorAll('.field-error').forEach((e) => (e.textContent = ''));
    const val = (k) => (form.elements[k].value === '' ? null : Number(form.elements[k].value));
    const body = { safe_withdrawal_rate: val('safe_withdrawal_rate'), return_rates: { conservative: val('return_rates.conservative'), base: val('return_rates.base'), optimistic: val('return_rates.optimistic') },
      monthly_contribution_override: val('monthly_contribution_override'), emergency_target_months: val('emergency_target_months'),
      budget_targets: { needs: val('budget_targets.needs'), wants: val('budget_targets.wants'), savings: val('budget_targets.savings') } };
    saveBtn.disabled = true;
    try { await api('/finance/assumptions', { method: 'PUT', body }); toast('Assumptions saved'); }
    catch (e) {
      aerr.hidden = false;
      aerr.replaceChildren(h('ul', null, (e.errors.length ? e.errors.map((x) => `${x.field}: ${x.msg}`) : [e.message]).map((m) => h('li', null, m))));
      for (const x of e.errors) { const t = document.getElementById('aserr_' + x.field); if (t) t.textContent = x.msg; }
    } finally { saveBtn.disabled = false; }
  });
  frag.append(h('section', { class: 'card' }, h('h2', null, 'Assumptions'), a ? [aerr, form] : h('div', { class: 'empty' }, 'Assumptions unavailable.')));

  // history
  const hist = imps?.imports || imps || [];
  const histCard = h('section', { class: 'card' }, h('h2', null, 'Import history'));
  if (!Array.isArray(hist) || !hist.length) histCard.append(h('div', { class: 'empty' }, 'No imports yet.'));
  else histCard.append(h('div', { class: 'table-wrap' }, h('table', null,
    h('thead', null, h('tr', null, ['When', 'File', 'Status', 'Income', 'Txns', 'Balances', 'Business', 'Message'].map((t, i) => h('th', { class: i >= 3 && i < 7 ? 'num' : '' }, t)))),
    h('tbody', null, hist.map((r) => h('tr', null, h('td', null, r.imported_at ? new Date(r.imported_at).toLocaleString() : '–'), h('td', null, r.file), h('td', null, badge(r.status)),
      ['rows_income', 'rows_transactions', 'rows_balances', 'rows_business'].map((k) => h('td', { class: 'num' }, r[k] ?? '–')), h('td', { class: 'wrap' }, r.message || '')))))));
  frag.append(histCard);
  return frag;
}

// ---------- router ----------
const VIEWS = { overview: viewOverview, money: viewMoney, milestones: viewMilestones, forecast: viewForecast, career: viewCareer, settings: viewSettings };
let renderSeq = 0;
async function render() {
  const tab = VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : 'overview';
  document.querySelectorAll('#tabs a').forEach((a) => (a.dataset.tab === tab ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  const seq = ++renderSeq, scroll = window.scrollY;
  try {
    const node = await VIEWS[tab]();
    if (seq !== renderSeq) { pending = []; return; }
    destroyCharts();
    view.replaceChildren(node);
    mountCharts();
    window.scrollTo(0, scroll);
  } catch (e) {
    if (seq !== renderSeq) return;
    view.replaceChildren(emptyState('Something went wrong: ' + e.message));
    console.error(e);
  }
}
window.addEventListener('hashchange', () => { window.scrollTo(0, 0); render(); view.focus({ preventScroll: true }); });
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => render());
refreshHeader();
render();
