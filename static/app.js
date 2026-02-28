/* Trend2Biz Web Dashboard — app.js */
'use strict';

const API = '/api/v1';
const POLL_INTERVAL = 1500;
const POLL_TIMEOUT = 120000;

// ── State ──────────────────────────────────────────────────────────────────

let currentDate = todayStr();
let currentSince = 'daily';
let currentLanguage = '';   // '' = all languages (client-side filter)
let tableRows = [];   // merged rows

// ── Utilities ──────────────────────────────────────────────────────────────

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function shiftDate(dateStr, days) {
  const d = new Date(dateStr + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

async function apiFetch(path, options = {}) {
  const res = await fetch(path, options);
  return res;
}

// ── Job Polling ────────────────────────────────────────────────────────────

async function pollJob(jobId) {
  const deadline = Date.now() + POLL_TIMEOUT;
  while (Date.now() < deadline) {
    const res = await apiFetch(`${API}/jobs/${jobId}`);
    if (!res.ok) throw new Error(`Job poll error: ${res.status}`);
    const job = await res.json();
    if (job.status === 'succeeded') return job;
    if (job.status === 'failed') throw new Error(job.error || 'Job failed');
    await sleep(POLL_INTERVAL);
  }
  throw new Error('Job timed out');
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ── Data Loading ───────────────────────────────────────────────────────────

async function fetchSnapshotItems(date, since) {
  const params = new URLSearchParams({ since, date, limit: 50 });
  const res = await apiFetch(`${API}/trending/snapshots?${params}`);
  if (res.status === 404) return null;   // no data for this date
  if (!res.ok) throw new Error(`Snapshot fetch error: ${res.status}`);
  const data = await res.json();
  return data.items || [];
}

async function fetchAvailableDates(since) {
  const params = new URLSearchParams({ since });
  const res = await apiFetch(`${API}/trending/snapshots/dates?${params}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.dates || [];
}

async function fetchProjects() {
  const res = await apiFetch(`${API}/projects?limit=200`);
  if (!res.ok) throw new Error(`Projects fetch error: ${res.status}`);
  const data = await res.json();
  return data.items || [];
}

async function fetchProjectDetail(projectId) {
  const res = await apiFetch(`${API}/projects/${projectId}`);
  if (!res.ok) throw new Error(`Project detail error: ${res.status}`);
  return await res.json();
}

// Snapshot items now include project_id, latest_biz, latest_score from the backend —
// no separate projects fetch required.
function mergeRows(snapshotItems) {
  return snapshotItems.map(si => {
    return {
      rank: si.rank,
      repo_full_name: si.repo_full_name,
      description: si.description,
      primary_language: si.primary_language,
      stars_delta: si.stars_delta_window,
      stars_total: si.stars_total_hint,
      project_id: si.project_id || null,
      analyzed: !!(si.latest_score || si.latest_biz),
      score: si.latest_score || null,
      biz: si.latest_biz || null,
    };
  }).sort((a, b) => a.rank - b.rank);
}

// ── Trigger Trending Fetch ─────────────────────────────────────────────────

async function triggerFetch(since, language = 'all') {
  setStatus('正在抓取 GitHub Trending...', 'info');
  const res = await apiFetch(`${API}/trending/snapshots:fetch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ since, language }),
  });
  if (res.status !== 202) {
    const err = await res.text();
    throw new Error(`Fetch failed: ${err}`);
  }
  const { job_id } = await res.json();
  setStatus(`抓取任务已启动 (${job_id})，等待完成...`, 'info');
  await pollJob(job_id);
}

// ── Analysis ──────────────────────────────────────────────────────────────

async function analyzeProject(projectId, rowIdx) {
  const row = tableRows[rowIdx];
  if (!row) return;

  // Update UI to analyzing state
  row._analyzing = true;
  renderAnalysisCell(rowIdx, 'metrics');

  try {
    // Step 1: metrics:refresh
    const r1 = await apiFetch(`${API}/projects/${projectId}/metrics:refresh`, { method: 'POST' });
    if (r1.status !== 202) throw new Error(`metrics:refresh failed: ${r1.status}`);
    const { job_id: j1 } = await r1.json();
    await pollJob(j1);

    // Step 2: biz:generate (pass API key/provider from settings if configured)
    renderAnalysisCell(rowIdx, 'biz');
    const { ai_api_key, ai_provider } = getSettings();
    const bizPayload = { model: 'rule-v1' };
    if (ai_api_key) { bizPayload.api_key = ai_api_key; bizPayload.provider = ai_provider || 'anthropic'; }
    const r2 = await apiFetch(`${API}/projects/${projectId}/biz-profiles:generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(bizPayload),
    });
    if (r2.status !== 202) throw new Error(`biz:generate failed: ${r2.status}`);
    const { job_id: j2 } = await r2.json();
    await pollJob(j2);

    // Step 3: reload project detail
    const detail = await fetchProjectDetail(projectId);
    row.analyzed = true;
    row.score = detail.latest_score;
    row.biz = detail.latest_biz_profile;
    row._analyzing = false;
    renderAnalysisCell(rowIdx, 'done');
    updateSummary();

  } catch (err) {
    row._analyzing = false;
    renderAnalysisCell(rowIdx, 'error', err.message);
  }
}

async function autoAnalyzeAll() {
  const queue = tableRows
    .map((row, idx) => ({ row, idx }))
    .filter(({ row }) => !row.analyzed && row.project_id);

  if (queue.length && !getSettings().ai_api_key) {
    setStatus('提示：未配置 API Key，将使用免费翻译（质量较低）。点击 ⚙ 在设置中配置。', 'info');
    setTimeout(clearStatus, 7000);
  }

  // Mark all as waiting and update UI
  for (const { row, idx } of queue) {
    row._waiting = true;
    const cell = document.getElementById(`analysis-${idx}`);
    if (cell) cell.querySelector('.cell').innerHTML = analysisHtml(row, idx);
  }

  // Process one at a time (sequential to avoid API overload)
  for (const { row, idx } of queue) {
    if (!row._waiting) continue;  // skip if manually triggered already
    row._waiting = false;
    await analyzeProject(row.project_id, idx);
  }
}

// ── Rendering ──────────────────────────────────────────────────────────────

function langDotHtml(lang) {
  if (!lang) return '<span class="lang-dot"></span>';
  return `<span class="lang-dot"></span>`;
}

function gradeBadgeHtml(grade) {
  return `<span class="grade-badge grade-${grade}">${grade}</span>`;
}

function analysisHtml(row, rowIdx) {
  if (row._analyzing === 'metrics') {
    return `<div class="analyze-progress"><div class="spinner"></div> 采集指标...</div>`;
  }
  if (row._analyzing === 'biz') {
    return `<div class="analyze-progress"><div class="spinner"></div> 生成分析...</div>`;
  }
  if (row._analyzing === 'error') {
    return `<div class="analyze-progress" style="color:var(--danger)">&#10005; 失败</div>`;
  }

  if (row._waiting) {
    return `<span class="analyze-waiting">等待分析</span>`;
  }

  if (row.analyzed && (row.score || row.biz)) {
    const s = row.score;
    const b = row.biz;
    const grade = s ? (s.grade || '?') : null;
    const total = s && typeof s.total === 'number' ? s.total.toFixed(1) : null;
    const catVal = b ? (b.category || '') : '';
    const monoList = b && b.monetization_candidates ? b.monetization_candidates.slice(0, 3) : [];
    return `
      <div class="score-info">
        ${grade ? `<div class="score-row">
          ${gradeBadgeHtml(grade)}
          ${total ? `<span class="score-num">${total}</span>` : ''}
        </div>` : ''}
        ${catVal ? `<button class="kw-tag kw-cat" data-kw="${escHtml(catVal)}">${escHtml(catVal)}</button>` : ''}
        ${monoList.length ? `<div class="kw-tags">${monoList.map(m => `<button class="kw-tag kw-mono" data-kw="${escHtml(m)}">${escHtml(m)}</button>`).join('')}</div>` : ''}
      </div>`;
  }

  if (!row.project_id) {
    return `<span style="color:var(--text-dim);font-size:12px">— 未入库</span>`;
  }

  return `<button class="analyze-btn" onclick="startAnalysis(${rowIdx})">&#9889; 分析</button>`;
}

// rowPairs: [{row, idx}] where idx is the original index in tableRows
function buildTableHtml(rowPairs) {
  if (!rowPairs.length) return '<div class="empty-state"><p>无匹配项目</p></div>';

  const totalAnalysed = tableRows.filter(r => r.analyzed).length;
  const filterNote = rowPairs.length < tableRows.length
    ? ` &nbsp;·&nbsp; 筛选显示 ${rowPairs.length} 个` : '';

  let html = `
    <div class="table-summary">已分析 ${totalAnalysed} / ${tableRows.length} 个项目${filterNote}</div>
    <table class="trend-table">
      <thead>
        <tr>
          <th class="col-rank">#</th>
          <th class="col-repo">项目</th>
          <th class="col-lang">语言</th>
          <th class="col-stars">&#9733; 今日新增</th>
          <th class="col-stars-total">&#9733; Star总数</th>
          <th class="col-analysis">商业分析</th>
        </tr>
      </thead>
      <tbody id="table-body">
  `;

  rowPairs.forEach(({row, idx}) => {
    const parts = row.repo_full_name.split('/');
    const owner = parts[0] || '';
    const name = parts[1] || row.repo_full_name;
    const lang = row.primary_language || '';
    const delta = row.stars_delta != null ? `+${row.stars_delta}` : '—';
    const deltaClass = row.stars_delta ? '' : 'none';
    const totalStars = row.stars_total != null ? row.stars_total.toLocaleString() : '—';
    const rowClass = row.analyzed ? 'analyzed' : (row._analyzing ? 'analyzing' : '');
    const chineseDesc = (row.biz && row.biz.description_zh)
      ? row.biz.description_zh
      : (row.biz && row.biz.scenarios && row.biz.scenarios.length)
        ? row.biz.scenarios.slice(0, 2).join(' · ')
        : null;
    const descText = chineseDesc || row.description;

    html += `
      <tr class="trend-row ${rowClass}" id="row-${idx}" data-lang="${lang}">
        <td class="col-rank"><div class="cell"><span class="rank-num">${row.rank}</span></div></td>
        <td class="col-repo">
          <div class="cell" style="flex-direction:column;align-items:flex-start;gap:2px;">
            <a class="repo-name" href="https://github.com/${row.repo_full_name}" target="_blank">
              <span class="repo-owner">${owner}/</span>${name}
            </a>
            ${descText ? `<div class="repo-desc">${escHtml(descText)}</div>` : ''}
          </div>
        </td>
        <td class="col-lang">
          <div class="cell">
            <span class="lang-pill">
              ${langDotHtml(lang)}
              ${lang || '—'}
            </span>
          </div>
        </td>
        <td class="col-stars">
          <div class="cell"><span class="stars-delta ${deltaClass}">${delta}</span></div>
        </td>
        <td class="col-stars-total">
          <div class="cell"><span class="stars-total">${totalStars}</span></div>
        </td>
        <td class="col-analysis" id="analysis-${idx}">
          <div class="cell">${analysisHtml(row, idx)}</div>
        </td>
      </tr>`;
  });

  html += '</tbody></table>';
  return html;
}

function getFilteredRowPairs() {
  const pairs = tableRows.map((row, idx) => ({row, idx}));
  if (!currentLanguage) return pairs;
  return pairs.filter(({row}) => (row.primary_language || '') === currentLanguage);
}

function renderTable() {
  const content = document.getElementById('content');
  if (!content) return;
  content.innerHTML = buildTableHtml(getFilteredRowPairs());
}

function renderAnalysisCell(rowIdx, state, errMsg) {
  const cell = document.getElementById(`analysis-${rowIdx}`);
  if (!cell) return;
  const row = tableRows[rowIdx];
  row._analyzing = state === 'done' || state === 'error' ? false : state;
  if (state === 'error') row._analyzing = 'error';
  cell.querySelector('.cell').innerHTML = analysisHtml(row, rowIdx);
  // Update row class
  const rowEl = document.getElementById(`row-${rowIdx}`);
  if (rowEl) {
    rowEl.className = `trend-row ${row.analyzed ? 'analyzed' : (row._analyzing ? 'analyzing' : '')}`;
  }
}

function updateSummary() {
  const el = document.querySelector('.table-summary');
  if (!el) return;
  const analysedCount = tableRows.filter(r => r.analyzed).length;
  const displayedCount = currentLanguage
    ? tableRows.filter(r => (r.primary_language || '') === currentLanguage).length
    : tableRows.length;
  const filterNote = displayedCount < tableRows.length ? ` &nbsp;·&nbsp; 筛选显示 ${displayedCount} 个` : '';
  el.innerHTML = `已分析 ${analysedCount} / ${tableRows.length} 个项目${filterNote}`;
}

function escHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Status Bar ─────────────────────────────────────────────────────────────

function setStatus(msg, type = 'info') {
  const bar = document.getElementById('status-bar');
  bar.textContent = msg;
  bar.className = `status-bar${type === 'error' ? ' error' : type === 'success' ? ' success' : ''}`;
  bar.style.display = msg ? 'block' : 'none';
}

function clearStatus() {
  setStatus('');
}

// ── Public action (called from HTML) ──────────────────────────────────────

window.startAnalysis = function(rowIdx) {
  const row = tableRows[rowIdx];
  if (!row || row._analyzing || !row.project_id) return;
  if (!getSettings().ai_api_key) {
    openSettingsModal(true);   // open settings with "no key" warning
    return;
  }
  analyzeProject(row.project_id, rowIdx);
};

// ── Main Load Flow ─────────────────────────────────────────────────────────

async function loadDashboard(date, since, forceRefetch = false) {
  const content = document.getElementById('content');
  content.innerHTML = '<div class="loading-state" id="loading"><div class="spinner"></div><span>加载中...</span></div>';
  clearStatus();

  try {
    let snapshotItems = await fetchSnapshotItems(date, since);

    if (snapshotItems === null) {
      // No data for this date
      if (date !== todayStr()) {
        // Historical date with no data — find nearest available date
        let nearestDate = null;
        try {
          const dates = await fetchAvailableDates(since);
          nearestDate = dates.find(d => d < date) || dates[0] || null;
        } catch (_) { /* ignore */ }

        const hintMsg = nearestDate
          ? `${date} 的 ${since} 数据尚未采集，最近有数据的日期是 ${nearestDate}`
          : `${date} 尚无 ${since} Trending 数据，过去的数据未曾采集`;
        const btnHtml = nearestDate
          ? `<button class="no-data-nav-btn" id="no-data-nav-btn">跳转到 ${nearestDate}</button>`
          : '';
        content.innerHTML = `
          <div class="no-data-hint">
            <h3>暂无数据</h3>
            <p>${escHtml(hintMsg)}</p>
            ${btnHtml}
          </div>`;
        if (nearestDate) {
          document.getElementById('no-data-nav-btn').addEventListener('click', () => {
            currentDate = nearestDate;
            document.getElementById('date-input').value = nearestDate;
            document.getElementById('btn-next').disabled = nearestDate >= todayStr();
            loadDashboard(nearestDate, since);
          });
        }
        document.getElementById('footer-info').textContent = `${date} · ${since} · 无数据`;
        return;
      }

      // Today: auto-fetch if setting is on, otherwise prompt
      const s = getSettings();
      if (forceRefetch || s.auto_fetch) {
        setStatus('正在抓取今日 Trending...', 'info');
        await triggerFetch(since);
        snapshotItems = await fetchSnapshotItems(date, since) || [];
        clearStatus();
      } else {
        content.innerHTML = `
          <div class="empty-state">
            <h3>今日数据未抓取</h3>
            <p>点击按钮抓取今日 GitHub Trending</p>
            <button class="empty-fetch-btn" id="empty-fetch-btn">&#9889; 抓取今日 Trending</button>
          </div>`;
        document.getElementById('empty-fetch-btn').addEventListener('click', () => {
          document.getElementById('empty-fetch-btn').disabled = true;
          document.getElementById('empty-fetch-btn').textContent = '抓取中...';
          loadDashboard(date, since, true);
        });
        document.getElementById('footer-info').textContent = `${date} · ${since} · 无数据`;
        return;
      }
    }

    // Snapshot items already include project_id + biz + score from the enriched API
    tableRows = mergeRows(snapshotItems);

    renderTable();
    document.getElementById('footer-info').textContent =
      `${date} · ${since} · ${tableRows.length} 个项目`;
    autoAnalyzeAll();

  } catch (err) {
    content.innerHTML = `<div class="empty-state"><h3>加载失败</h3><p>${escHtml(err.message)}</p></div>`;
    setStatus(`错误: ${err.message}`, 'error');
  }
}

// ── Init ───────────────────────────────────────────────────────────────────

function init() {
  const dateInput    = document.getElementById('date-input');
  const sinceSelect  = document.getElementById('since-select');
  const btnPrev      = document.getElementById('btn-prev');
  const btnNext      = document.getElementById('btn-next');
  const btnFetch     = document.getElementById('btn-fetch');
  const btnRefresh   = document.getElementById('btn-refresh');
  const langSelect   = document.getElementById('lang-select');
  const btnSettings  = document.getElementById('btn-settings');

  // Set initial values
  dateInput.value = currentDate;
  dateInput.max = todayStr();
  sinceSelect.value = currentSince;

  // Language filter (client-side)
  langSelect.addEventListener('change', () => {
    currentLanguage = langSelect.value;
    if (tableRows.length) renderTable();
  });

  // Settings modal
  btnSettings.addEventListener('click', openSettingsModal);

  // Date navigation
  btnPrev.addEventListener('click', () => {
    currentDate = shiftDate(currentDate, -1);
    dateInput.value = currentDate;
    loadDashboard(currentDate, currentSince);
  });

  btnNext.addEventListener('click', () => {
    const next = shiftDate(currentDate, 1);
    if (next > todayStr()) return;
    currentDate = next;
    dateInput.value = currentDate;
    loadDashboard(currentDate, currentSince);
  });

  dateInput.addEventListener('change', () => {
    currentDate = dateInput.value;
    loadDashboard(currentDate, currentSince);
  });

  sinceSelect.addEventListener('change', () => {
    currentSince = sinceSelect.value;
    loadDashboard(currentDate, currentSince);
  });

  btnFetch.addEventListener('click', () => {
    currentDate = todayStr();
    dateInput.value = currentDate;
    btnNext.disabled = true;
    loadDashboard(currentDate, currentSince, true);
  });

  btnRefresh.addEventListener('click', () => {
    loadDashboard(currentDate, currentSince);
  });

  // Disable next button when viewing today
  dateInput.addEventListener('change', () => {
    btnNext.disabled = currentDate >= todayStr();
  });

  // Initial load
  loadDashboard(currentDate, currentSince);
}

// ── Settings ────────────────────────────────────────────────────────────────

function getSettings() {
  try { return JSON.parse(localStorage.getItem('t2b_settings') || '{}'); }
  catch { return {}; }
}

function saveSettings(s) {
  localStorage.setItem('t2b_settings', JSON.stringify(s));
}

function openSettingsModal(warnNoKey = false) {
  const s = getSettings();
  document.getElementById('settings-provider').value = s.ai_provider || 'anthropic';
  document.getElementById('settings-api-key').value = s.ai_api_key || '';
  document.getElementById('settings-auto-fetch').checked = !!s.auto_fetch;
  document.getElementById('settings-nokey-warn').style.display = warnNoKey ? 'block' : 'none';
  document.getElementById('settings-modal').style.display = 'flex';
  if (warnNoKey) document.getElementById('settings-api-key').focus();
}

function closeSettingsModal() {
  document.getElementById('settings-modal').style.display = 'none';
}

function saveSettingsModal() {
  const s = {
    ai_provider: document.getElementById('settings-provider').value,
    ai_api_key:  document.getElementById('settings-api-key').value.trim(),
    auto_fetch:  document.getElementById('settings-auto-fetch').checked,
  };
  saveSettings(s);
  closeSettingsModal();
  setStatus('设置已保存', 'success');
  setTimeout(clearStatus, 2000);
}

// ── Keyword Modal ───────────────────────────────────────────────────────────

function openKeywordModal(keyword) {
  const matches = tableRows.filter(r => {
    if (!r.biz) return false;
    return r.biz.category === keyword
      || (r.biz.scenarios || []).includes(keyword)
      || (r.biz.monetization_candidates || []).includes(keyword)
      || (r.biz.delivery_forms || []).includes(keyword);
  });

  document.getElementById('kw-modal-title').textContent = `"${keyword}" — ${matches.length} 个项目`;

  const body = document.getElementById('kw-modal-body');
  if (!matches.length) {
    body.innerHTML = '<p class="kw-empty">暂无匹配项目</p>';
  } else {
    body.innerHTML = matches.map(r => {
      const parts = r.repo_full_name.split('/');
      const owner = parts[0] || '';
      const name = parts[1] || r.repo_full_name;
      const desc = (r.biz && r.biz.description_zh) || r.description || '';
      const grade = r.score ? r.score.grade : null;
      const stars = r.stars_total != null ? r.stars_total.toLocaleString() : '—';
      return `<div class="kw-project-row">
        ${grade ? gradeBadgeHtml(grade) : '<span style="width:24px;flex-shrink:0"></span>'}
        <div class="kw-project-info">
          <a class="kw-repo-link" href="https://github.com/${r.repo_full_name}" target="_blank">
            <span class="repo-owner">${escHtml(owner)}/</span>${escHtml(name)}
          </a>
          ${desc ? `<div class="kw-desc">${escHtml(desc)}</div>` : ''}
        </div>
        <span class="kw-stars">&#9733; ${stars}</span>
      </div>`;
    }).join('');
  }

  document.getElementById('kw-modal').style.display = 'flex';
}

function closeKeywordModal() {
  document.getElementById('kw-modal').style.display = 'none';
}

// Event delegation: kw-tag clicks anywhere in the document
document.addEventListener('click', e => {
  const tag = e.target.closest('[data-kw]');
  if (tag) {
    e.stopPropagation();
    openKeywordModal(tag.dataset.kw);
    return;
  }
  // Click on the backdrop (the modal overlay itself) closes it
  if (e.target.id === 'kw-modal') closeKeywordModal();
});

document.addEventListener('DOMContentLoaded', () => {
  // Keyword modal
  document.getElementById('kw-modal-close').addEventListener('click', closeKeywordModal);

  // Settings modal
  document.getElementById('settings-close-btn').addEventListener('click', closeSettingsModal);
  document.getElementById('settings-save-btn').addEventListener('click', saveSettingsModal);
  document.getElementById('settings-modal').addEventListener('click', e => {
    if (e.target.id === 'settings-modal') closeSettingsModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeSettingsModal(); closeKeywordModal(); }
  });

  init();
});
