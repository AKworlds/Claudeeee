// Login page logic (kept external: CSP is script-src 'self').
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

const form = document.getElementById('login-form');
const err = document.getElementById('login-error');
const btn = document.getElementById('login-btn');
const pw = document.getElementById('password');

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  err.hidden = true;
  btn.disabled = true;
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Dash': '1' },
      body: JSON.stringify({ password: pw.value }),
    });
    if (res.ok) { location.replace('/'); return; }
    let msg = 'Sign in failed';
    try { const d = await res.json(); if (typeof d.detail === 'string') msg = d.detail; } catch { /* ignore */ }
    err.textContent = msg;
    err.hidden = false;
    pw.select();
  } catch (e) {
    err.textContent = 'Network error: ' + e.message;
    err.hidden = false;
  } finally { btn.disabled = false; }
});
