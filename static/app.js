/* Trend2Biz Web Dashboard — app.js */
'use strict';

const API = '/api/v1';
const POLL_INTERVAL = 1500;
const POLL_TIMEOUT = 120000;

// ── State ──────────────────────────────────────────────────────────────────

let currentDate = todayStr();
let currentSince = 'daily';
let currentLanguage = '';   // '' = all languages (client-side filter)
let currentView = 'trending';  // 'trending' | 'watchlist' | 'search'
let tableRows = [];   // merged rows
let watchlistSet = new Set();  // project_ids currently in watchlist
let searchResults = [];        // rows from search API
let _pendingAnalysisRowIdx = null;   // set when warning modal opens with a pending row
let _serverHasKey = false;           // populated from /api/v1/version on init
let _serverHasGithubToken = false;
let _snapshotCapturedAt = null;      // ISO string of current snapshot's captured_at

// ── Utilities ──────────────────────────────────────────────────────────────

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function shiftDate(dateStr, days) {
  const d = new Date(dateStr + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function _getAccessToken() {
  try { return localStorage.getItem('t2b_access_token') || ''; } catch { return ''; }
}

async function apiFetch(path, options = {}) {
  const token = _getAccessToken();
  if (token) {
    options.headers = Object.assign({ 'Authorization': `Bearer ${token}` }, options.headers || {});
  }
  const res = await fetch(path, options);
  if (res.status === 401) {
    const tok = prompt('🔒 此实例需要访问密码，请输入 Access Token:');
    if (tok) {
      try { localStorage.setItem('t2b_access_token', tok); } catch {}
      return apiFetch(path, options);
    }
  }
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
  _snapshotCapturedAt = data.snapshot ? data.snapshot.captured_at : null;
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

async function loadWatchlist() {
  try {
    const res = await apiFetch(`${API}/watchlist`);
    if (!res.ok) return;
    const data = await res.json();
    watchlistSet = new Set((data.items || []).map(w => w.project_id));
  } catch { /* ignore */ }
}

async function toggleWatchlist(projectId, rowIdx) {
  const inList = watchlistSet.has(projectId);
  try {
    if (inList) {
      await apiFetch(`${API}/watchlist/${projectId}`, { method: 'DELETE' });
      watchlistSet.delete(projectId);
    } else {
      await apiFetch(`${API}/watchlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId }),
      });
      watchlistSet.add(projectId);
    }
    // Update just the star icon for this row
    const starBtn = document.querySelector(`.watch-btn[data-pid="${projectId}"]`);
    if (starBtn) starBtn.textContent = watchlistSet.has(projectId) ? '★' : '☆';
    // If we're in watchlist view and we just removed, re-render
    if (currentView === 'watchlist' && !watchlistSet.has(projectId)) renderTable();
  } catch (e) {
    setStatus(`关注列表操作失败: ${e.message}`, 'error');
    setTimeout(clearStatus, 3000);
  }
}

function showFreshness(capturedAtIso) {
  const el = document.getElementById('freshness-indicator');
  if (!el) return;
  if (!capturedAtIso) { el.textContent = ''; el.className = 'freshness-indicator'; return; }
  const now = Date.now();
  const then = new Date(capturedAtIso).getTime();
  const diffMs = now - then;
  const diffH = diffMs / 3600000;
  let label, cls;
  if (diffH < 2) {
    const m = Math.round(diffMs / 60000);
    label = `📡 ${m}分钟前采集`;
    cls = 'freshness-indicator fresh';
  } else if (diffH < 24) {
    label = `📡 ${Math.floor(diffH)}小时前采集`;
    cls = 'freshness-indicator stale';
  } else {
    const d = Math.floor(diffH / 24);
    label = `⚠️ ${d}天前采集`;
    cls = 'freshness-indicator old';
  }
  el.textContent = label;
  el.className = cls;
}

async function doSearch(q, opts) {
  opts = opts || {};
  const advGrade    = opts.grade    || (document.getElementById('adv-grade')    ? document.getElementById('adv-grade').value    : '');
  const advCategory = opts.category || (document.getElementById('adv-category') ? document.getElementById('adv-category').value : '');
  const advMinStars = opts.min_stars !== undefined ? opts.min_stars : (document.getElementById('adv-min-stars') ? parseInt(document.getElementById('adv-min-stars').value, 10) || 0 : 0);
  const hasFilter   = advGrade || advCategory || advMinStars > 0;

  // Require either keyword or at least one filter
  if (!q && !hasFilter) return;
  currentView = 'search';
  const content = document.getElementById('content');
  content.innerHTML = '<div class="loading-state"><div class="spinner"></div><span>搜索中...</span></div>';
  try {
    const params = { limit: 50 };
    if (q) params.q = q;
    if (advGrade)    params.grade = advGrade;
    if (advCategory) params.category = advCategory;
    if (advMinStars > 0) params.min_stars = advMinStars;
    const res = await apiFetch(`${API}/projects/search?${new URLSearchParams(params)}`);
    if (!res.ok) throw new Error(`Search error: ${res.status}`);
    const data = await res.json();
    searchResults = (data.items || []).map(p => ({
      rank: null,
      repo_full_name: p.repo_full_name,
      description: null,
      primary_language: p.primary_language,
      stars_delta: null,
      stars_total: null,
      project_id: p.project_id,
      analyzed: !!(p.latest_score || p.latest_biz_profile),
      score: p.latest_score || null,
      biz: p.latest_biz_profile || null,
    }));
    tableRows = searchResults;
    renderTable();
    let label = q ? `搜索 "${q}"` : '高级筛选';
    if (advGrade)    label += ` · 评级:${advGrade}`;
    if (advCategory) label += ` · 赛道:${advCategory}`;
    if (advMinStars > 0) label += ` · Stars≥${advMinStars}`;
    document.getElementById('footer-info').textContent = `${label} · ${searchResults.length} 个结果`;
  } catch (e) {
    content.innerHTML = `<div class="empty-state"><h3>搜索失败</h3><p>${escHtml(e.message)}</p></div>`;
  }
}

async function doExport(fmt) {
  const params = new URLSearchParams({ since: currentSince, date: currentDate, format: fmt });
  try {
    const res = await apiFetch(`${API}/trending/snapshots/export?${params}`);
    if (!res.ok) { alert('导出失败：' + res.status); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `trend2biz-${currentDate}-${currentSince}.${fmt}`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1000);
  } catch (e) {
    alert('导出失败：' + e.message);
  }
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

function getDisplayRows() {
  if (currentView === 'watchlist') {
    return tableRows.filter(r => r.project_id && watchlistSet.has(r.project_id));
  }
  return tableRows;
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
    const { ai_api_key, ai_provider, ai_model } = getSettings();
    const bizPayload = { model: ai_model || 'rule-v1' };
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

  const s = getSettings();
  if (queue.length && (s.ai_model || 'rule-v1') === 'llm-v1' && !s.ai_api_key && !_serverHasKey) {
    setStatus('提示：未配置 API Key，无法使用 llm-v1 分析。点击 ⚙ 在设置中配置。', 'info');
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
        <div class="score-row">
          ${grade ? gradeBadgeHtml(grade) : ''}
          ${total ? `<span class="score-num">${total}</span>` : ''}
          ${row.project_id ? `<button class="report-btn" onclick="generateReport('${row.project_id}')" title="生成投资分析报告">&#128202;<span class="report-label"> 报告</span></button>` : ''}
          ${row.project_id ? `<button class="reanalyze-btn" onclick="startAnalysis(${rowIdx})" title="重新分析（刷新指标+评分）">&#9889;</button>` : ''}
        </div>
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
  if (!rowPairs.length) {
    const emptyMsg = currentView === 'watchlist' ? '关注列表为空，点击表格中的 ☆ 添加项目'
                   : currentView === 'search' ? '未找到匹配项目' : '无匹配项目';
    return `<div class="empty-state"><p>${emptyMsg}</p></div>`;
  }

  const displayRows = getDisplayRows();
  const totalAnalysed = displayRows.filter(r => r.analyzed).length;
  const filterNote = rowPairs.length < displayRows.length
    ? ` &nbsp;·&nbsp; 筛选显示 ${rowPairs.length} 个` : '';
  const viewLabel = currentView === 'watchlist' ? ' · ★ 关注列表' : currentView === 'search' ? ' · 搜索结果' : '';

  let html = `
    <div class="table-summary">已分析 ${totalAnalysed} / ${displayRows.length} 个项目${filterNote}${viewLabel}</div>
    <table class="trend-table">
      <thead>
        <tr>
          <th class="col-rank">#</th>
          <th class="col-watch"></th>
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
    const bdPitch = (row.biz && row.biz.bd_pitch) || '';
    const chineseDesc = (row.biz && row.biz.description_zh)
      ? row.biz.description_zh
      : (row.biz && row.biz.scenarios && row.biz.scenarios.length)
        ? row.biz.scenarios.slice(0, 2).join(' · ')
        : null;
    const descText = chineseDesc || row.description;
    const rankDisplay = row.rank != null ? row.rank : '—';
    const isWatched = row.project_id && watchlistSet.has(row.project_id);
    const watchBtn = row.project_id
      ? `<button class="watch-btn${isWatched ? ' watched' : ''}" data-pid="${row.project_id}" title="${isWatched ? '取消关注' : '添加关注'}">${isWatched ? '★' : '☆'}</button>`
      : '';

    html += `
      <tr class="trend-row ${rowClass}" id="row-${idx}" data-lang="${lang}"${bdPitch ? ` data-rowidx="${idx}"` : ''}>
        <td class="col-rank"><div class="cell"><span class="rank-num">${rankDisplay}</span></div></td>
        <td class="col-watch"><div class="cell">${watchBtn}</div></td>
        <td class="col-repo">
          <div class="cell" style="flex-direction:column;align-items:flex-start;gap:2px;">
            <div class="repo-name-row">
              <a class="repo-name" href="https://github.com/${row.repo_full_name}" target="_blank">
                <span class="repo-owner">${owner}/</span>${name}
              </a>
              ${lang ? `<span class="lang-pill lang-mobile-only">${langDotHtml(lang)}${lang}</span>` : ''}
            </div>
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
          <div class="cell">
            <span class="stars-delta ${deltaClass}">${delta}</span>
            <span class="stars-total-mobile">&#9733; ${totalStars}</span>
          </div>
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
  const display = getDisplayRows();
  const pairs = display.map((row, i) => {
    // find real index in tableRows so analysis cell IDs match
    const idx = tableRows.indexOf(row);
    return { row, idx: idx >= 0 ? idx : i };
  });
  if (!currentLanguage) return pairs;
  return pairs.filter(({row}) => (row.primary_language || '') === currentLanguage);
}

function renderTable() {
  const content = document.getElementById('content');
  if (!content) return;
  content.innerHTML = buildTableHtml(getFilteredRowPairs());
  // Bind watch button clicks via event delegation on the table
  content.addEventListener('click', e => {
    const btn = e.target.closest('.watch-btn');
    if (!btn) return;
    const pid = btn.dataset.pid;
    const idx = tableRows.findIndex(r => r.project_id === pid);
    toggleWatchlist(pid, idx);
  }, { once: true });
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
  const s = getSettings();
  if ((s.ai_model || 'rule-v1') === 'llm-v1' && !s.ai_api_key && !_serverHasKey) {
    _pendingAnalysisRowIdx = rowIdx;
    openSettingsModal(true);   // open settings with "no key" warning
    return;
  }
  analyzeProject(row.project_id, rowIdx);
};

window.generateReport = async function(projectId) {
  // Open a blank window immediately (sync, inside click handler) to avoid popup blocking.
  // After the async work completes we navigate it to the real URL.
  const reportWin = window.open('', '_blank');
  if (reportWin) {
    reportWin.document.write('<p style="font-family:sans-serif;padding:40px;color:#64748b">正在生成报告，请稍候…</p>');
  }
  setStatus('正在生成报告…', 'info');
  try {
    const r = await apiFetch(`${API}/reports:generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, format: 'html', latest: true }),
    });
    if (!r.ok) {
      if (reportWin) reportWin.close();
      setStatus('报告生成失败', 'error'); setTimeout(clearStatus, 3000); return;
    }
    const { job_id, url } = await r.json();
    if (job_id) await pollJob(job_id);
    clearStatus();
    if (url) {
      if (reportWin) reportWin.location.href = url;
      else window.open(url, '_blank');
    }
  } catch (err) {
    if (reportWin) reportWin.close();
    setStatus(`报告生成失败: ${err.message}`, 'error');
    setTimeout(clearStatus, 3000);
  }
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
    currentView = 'trending';

    renderTable();
    showFreshness(_snapshotCapturedAt);
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
  btnSettings.addEventListener('click', () => openSettingsModal());

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

  // Version badge + server key status
  apiFetch('/api/v1/version').then(r => r.ok ? r.json() : null).then(data => {
    if (!data) return;
    const el = document.getElementById('version-badge');
    if (el) el.textContent = `v${data.version} · ${data.build}`;
    _serverHasKey = !!data.server_has_key;
    _serverHasGithubToken = !!data.server_has_github_token;
  }).catch(() => {});

  // Load initial watchlist
  loadWatchlist();

  // Watchlist tab button
  const btnWatchlist = document.getElementById('btn-watchlist');
  if (btnWatchlist) {
    btnWatchlist.addEventListener('click', () => {
      if (currentView === 'watchlist') {
        currentView = 'trending';
        btnWatchlist.classList.remove('active');
        renderTable();
      } else {
        currentView = 'watchlist';
        btnWatchlist.classList.add('active');
        renderTable();
      }
    });
  }

  // Search box
  const searchInput = document.getElementById('search-input');
  const searchBtn = document.getElementById('btn-search');
  const searchClear = document.getElementById('btn-search-clear');
  if (searchInput) {
    searchInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') doSearch(searchInput.value.trim());
      if (e.key === 'Escape') {
        searchInput.value = '';
        if (currentView === 'search') { currentView = 'trending'; renderTable(); }
        showFreshness(_snapshotCapturedAt);
        document.getElementById('footer-info').textContent = `${currentDate} · ${currentSince} · ${tableRows.length} 个项目`;
      }
    });
  }
  if (searchBtn) searchBtn.addEventListener('click', () => doSearch(searchInput ? searchInput.value.trim() : ''));
  if (searchClear) searchClear.addEventListener('click', () => {
    if (searchInput) searchInput.value = '';
    if (currentView === 'search') { currentView = 'trending'; tableRows = []; renderTable(); loadDashboard(currentDate, currentSince); }
  });

  // Advanced filter panel toggle
  const advToggleBtn = document.getElementById('btn-adv-toggle');
  const advPanel = document.getElementById('adv-panel');
  if (advToggleBtn && advPanel) {
    advToggleBtn.addEventListener('click', () => {
      const visible = advPanel.style.display !== 'none';
      advPanel.style.display = visible ? 'none' : 'flex';
      advToggleBtn.textContent = visible ? '高级 ▾' : '高级 ▴';
    });
  }
  const advSearchBtn = document.getElementById('btn-adv-search');
  if (advSearchBtn) {
    advSearchBtn.addEventListener('click', () => {
      doSearch(searchInput ? searchInput.value.trim() : '');
    });
  }
  // Auto-trigger search when advanced filter selects change
  ['adv-grade', 'adv-category'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => doSearch(searchInput ? searchInput.value.trim() : ''));
  });

  // Export dropdown
  const btnExportCsv = document.getElementById('btn-export-csv');
  const btnExportJson = document.getElementById('btn-export-json');
  if (btnExportCsv) btnExportCsv.addEventListener('click', () => doExport('csv'));
  if (btnExportJson) btnExportJson.addEventListener('click', () => doExport('json'));
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
  document.getElementById('settings-model').value = s.ai_model || 'rule-v1';
  document.getElementById('settings-provider').value = s.ai_provider || 'anthropic';
  document.getElementById('settings-api-key').value = s.ai_api_key || '';
  document.getElementById('settings-auto-fetch').checked = !!s.auto_fetch;
  document.getElementById('settings-nokey-warn').style.display = warnNoKey ? 'block' : 'none';
  document.getElementById('settings-modal').style.display = 'flex';
  if (warnNoKey) document.getElementById('settings-api-key').focus();

  // Load server key statuses
  apiFetch(`${API}/settings/llm-key-status`).then(r => r.ok ? r.json() : null).then(data => {
    const el = document.getElementById('settings-server-key-status');
    if (!el || !data) return;
    el.textContent = data.has_key
      ? `✓ 服务器已配置 ${data.provider || ''} Key (${data.masked || '***'})  [${data.source}]`
      : '服务器未配置 Key（使用本地 Key）';
    el.style.color = data.has_key ? 'var(--success)' : 'var(--text-dim)';
  }).catch(() => {});

  apiFetch(`${API}/settings/github-token-status`).then(r => r.ok ? r.json() : null).then(data => {
    const el = document.getElementById('settings-github-token-status');
    if (!el || !data) return;
    if (data.has_token) {
      const rl = data.rate_limit;
      const rateStr = rl && rl.remaining != null ? ` · API 剩余 ${rl.remaining}/${rl.limit}` : '';
      el.textContent = `✓ Token 已配置 (${data.masked || '***'})${rateStr}`;
      el.style.color = 'var(--success)';
    } else {
      el.textContent = '未配置（60 次/小时限额）';
      el.style.color = 'var(--text-dim)';
    }
  }).catch(() => {});
}

function closeSettingsModal() {
  document.getElementById('settings-modal').style.display = 'none';
}

async function saveSettingsModal() {
  const s = {
    ai_model:    document.getElementById('settings-model').value,
    ai_provider: document.getElementById('settings-provider').value,
    ai_api_key:  document.getElementById('settings-api-key').value.trim(),
    auto_fetch:  document.getElementById('settings-auto-fetch').checked,
  };
  saveSettings(s);

  // Save server-side LLM key if the field is filled
  const serverKeyInput = document.getElementById('settings-server-llm-key');
  if (serverKeyInput && serverKeyInput.value.trim()) {
    try {
      await apiFetch(`${API}/settings/llm-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: serverKeyInput.value.trim(), provider: s.ai_provider }),
      });
      serverKeyInput.value = '';
      _serverHasKey = true;
    } catch (e) { /* ignore */ }
  }

  // Save GitHub Token if provided
  const ghTokenInput = document.getElementById('settings-github-token');
  if (ghTokenInput && ghTokenInput.value.trim()) {
    try {
      await apiFetch(`${API}/settings/github-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: ghTokenInput.value.trim() }),
      });
      ghTokenInput.value = '';
      _serverHasGithubToken = true;
    } catch (e) { /* ignore */ }
  }

  closeSettingsModal();

  // Auto-retry analysis that triggered the "no key" warning
  if (_pendingAnalysisRowIdx !== null && (s.ai_api_key || _serverHasKey) && (s.ai_model || 'rule-v1') === 'llm-v1') {
    const pendingIdx = _pendingAnalysisRowIdx;
    _pendingAnalysisRowIdx = null;
    const pendingRow = tableRows[pendingIdx];
    if (pendingRow && !pendingRow._analyzing) {
      setStatus('Key 已保存，正在分析…', 'info');
      analyzeProject(pendingRow.project_id, pendingIdx);
      return;
    }
  }
  _pendingAnalysisRowIdx = null;

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
      const bdPitch = (r.biz && r.biz.bd_pitch) || '';
      const grade = r.score ? r.score.grade : null;
      const stars = r.stars_total != null ? r.stars_total.toLocaleString() : '—';
      return `<div class="kw-project-row">
        ${grade ? gradeBadgeHtml(grade) : '<span style="width:24px;flex-shrink:0"></span>'}
        <div class="kw-project-info">
          <a class="kw-repo-link" href="https://github.com/${r.repo_full_name}" target="_blank">
            <span class="repo-owner">${escHtml(owner)}/</span>${escHtml(name)}
          </a>
          ${desc ? `<div class="kw-desc">${escHtml(desc)}</div>` : ''}
          ${bdPitch ? `<div class="kw-bd-pitch">&#128172; ${escHtml(bdPitch)}</div>` : ''}
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

// ── Jobs Panel ─────────────────────────────────────────────────────────────

const JOB_TYPE_LABEL = {
  trending_fetch:  '抓取 Trending',
  metrics_refresh: '刷新指标',
  metrics_backfill:'补全星标历史',
  biz_generate:    'AI 分析',
  score_batch:     '批量评分',
};
const SCHED_LABEL = {
  daily_trending:          'daily/all trending',
  weekly_monthly_trending: 'weekly + monthly trending',
  metrics_refresh:         'top-200 指标刷新',
  biz_score:               '未分析项目 biz+score',
};

function fmtNextRun(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  const now = new Date();
  const diffMs = d - now;
  if (diffMs < 0) return '即将运行';
  const h = Math.floor(diffMs / 3600000);
  const m = Math.floor((diffMs % 3600000) / 60000);
  const hStr = h > 0 ? `${h}h ` : '';
  return `${hStr}${m}m 后 (${d.toUTCString().slice(17, 22)} UTC)`;
}

function fmtJobTime(isoStr) {
  if (!isoStr) return '—';
  return new Date(isoStr).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

async function loadJobsPanel() {
  // Today summary
  const summaryEl = document.getElementById('jobs-summary');
  summaryEl.textContent = '加载中…';
  try {
    const today = todayStr();
    const res = await apiFetch(`${API}/jobs?limit=200`);
    const data = res.ok ? await res.json() : { jobs: [] };
    const todayJobs = (data.jobs || []).filter(j => (j.created_at || '').startsWith(today));
    const counts = { queued: 0, running: 0, succeeded: 0, failed: 0 };
    todayJobs.forEach(j => { counts[j.status] = (counts[j.status] || 0) + 1; });
    const total = todayJobs.length;
    summaryEl.innerHTML = total === 0
      ? '<span class="jobs-empty">今日暂无任务</span>'
      : `<span class="jobs-stat">&#128202; 共 ${total}</span>` +
        (counts.running  ? `<span class="jobs-stat running">&#9654; 运行中 ${counts.running}</span>` : '') +
        (counts.queued   ? `<span class="jobs-stat queued">&#8987; 排队 ${counts.queued}</span>` : '') +
        `<span class="jobs-stat succeeded">&#10003; 成功 ${counts.succeeded}</span>` +
        (counts.failed   ? `<span class="jobs-stat failed">&#10007; 失败 ${counts.failed}</span>` : '');

    // Failed jobs list (last 20, any date)
    const failed = (data.jobs || []).filter(j => j.status === 'failed').slice(0, 20);
    const failCount = document.getElementById('jobs-fail-count');
    failCount.textContent = failed.length ? `(${failed.length})` : '';
    const failEl = document.getElementById('jobs-fail-list');
    if (!failed.length) {
      failEl.innerHTML = '<span class="jobs-empty">无失败任务 ✓</span>';
    } else {
      failEl.innerHTML = failed.map(j => {
        const label = JOB_TYPE_LABEL[j.job_type] || j.job_type;
        const p = j.payload || {};
        const detail = p.since ? `${p.since}/${p.language}` : (p.project_id || '').slice(0, 8);
        const t = fmtJobTime(j.created_at);
        const errMsg = j.error || '';
        const errShort = errMsg.length > 80 ? errMsg.slice(0, 80) + '…' : errMsg;
        return `<div class="jobs-fail-row">
          <div class="jobs-fail-main">
            <div class="jobs-fail-top">
              <span class="jobs-type-badge">${escHtml(label)}</span>
              <span class="jobs-fail-detail">${escHtml(detail)}</span>
              <span class="jobs-fail-time">${t}</span>
            </div>
            ${errShort ? `<div class="jobs-fail-error" title="${escHtml(errMsg)}">${escHtml(errShort)}</div>` : ''}
            <div class="jobs-retry-row">
              <select class="jobs-retry-delay-sel" data-jid="${j.job_id}">
                <option value="0">立即重试</option>
                <option value="10">10 分钟后</option>
                <option value="30">30 分钟后</option>
                <option value="60">1 小时后</option>
              </select>
              <button class="jobs-retry-btn" data-jid="${j.job_id}">重试</button>
            </div>
          </div>
        </div>`;
      }).join('');
    }
  } catch (e) {
    summaryEl.textContent = '加载失败';
  }

  // Scheduler status
  try {
    const sres = await apiFetch(`${API}/scheduler/status`);
    if (sres.ok) {
      const sdata = await sres.json();
      const schedSection = document.getElementById('jobs-sched-section');
      const schedEl = document.getElementById('jobs-schedule');
      if (sdata.enabled && sdata.jobs && sdata.jobs.length) {
        schedSection.style.display = '';
        schedEl.innerHTML = sdata.jobs.map(j => {
          const lbl = SCHED_LABEL[j.id] || j.id;
          const h = j.hour != null ? String(j.hour).padStart(2, '0') : '??';
          const m = j.minute != null ? String(j.minute).padStart(2, '0') : '??';
          return `<div class="jobs-sched-row" data-jid="${escHtml(j.id)}">
            <span class="jobs-sched-name">${escHtml(lbl)}</span>
            <span class="jobs-sched-next">${fmtNextRun(j.next_run)}</span>
            <input class="jobs-sched-hour" type="number" min="0" max="23" value="${h}" title="小时 (UTC 0-23)">
            <span class="jobs-sched-sep">:</span>
            <input class="jobs-sched-min" type="number" min="0" max="59" value="${m}" title="分钟 (0-59)">
            <button class="jobs-sched-save-btn" data-jid="${escHtml(j.id)}">保存</button>
          </div>`;
        }).join('');
      } else {
        schedSection.style.display = sdata.enabled ? '' : 'none';
        if (!sdata.enabled) schedEl.textContent = '（调度器未启用）';
      }
    }
  } catch (_) { /* ignore */ }
}

async function retryJob(jobId) {
  const btn = document.querySelector(`.jobs-retry-btn[data-jid="${jobId}"]`);
  const sel = document.querySelector(`.jobs-retry-delay-sel[data-jid="${jobId}"]`);
  const delayMinutes = sel ? parseInt(sel.value, 10) : 0;
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const res = await apiFetch(`${API}/jobs/${jobId}:retry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delay_minutes: delayMinutes }),
    });
    if (!res.ok) throw new Error(await res.text());
    const label = delayMinutes > 0 ? `${delayMinutes}分后` : '已入队';
    if (btn) { btn.textContent = label; }
    setTimeout(loadJobsPanel, delayMinutes > 0 ? 2000 : 1500);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '重试'; }
    alert('重试失败: ' + e.message);
  }
}

async function saveSchedJob(jobId) {
  const row = document.querySelector(`.jobs-sched-row[data-jid="${jobId}"]`);
  if (!row) return;
  const hourVal = parseInt(row.querySelector('.jobs-sched-hour').value, 10);
  const minVal  = parseInt(row.querySelector('.jobs-sched-min').value,  10);
  if (isNaN(hourVal) || isNaN(minVal) || hourVal < 0 || hourVal > 23 || minVal < 0 || minVal > 59) {
    alert('时间格式错误：小时 0-23，分钟 0-59');
    return;
  }
  const btn = row.querySelector('.jobs-sched-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const res = await apiFetch(`${API}/scheduler/reschedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId, hour: hourVal, minute: minVal }),
    });
    if (!res.ok) throw new Error(await res.text());
    if (btn) { btn.textContent = '已保存'; setTimeout(() => { if (btn) btn.textContent = '保存'; btn.disabled = false; }, 2000); }
    setTimeout(loadJobsPanel, 2200);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '保存'; }
    alert('保存失败: ' + e.message);
  }
}

function openJobsPanel() {
  document.getElementById('jobs-modal').style.display = 'flex';
  loadJobsPanel();
}
function closeJobsPanel() {
  document.getElementById('jobs-modal').style.display = 'none';
}

document.addEventListener('DOMContentLoaded', () => {
  // Keyword modal
  document.getElementById('kw-modal-close').addEventListener('click', closeKeywordModal);

  // Jobs panel
  document.getElementById('btn-jobs').addEventListener('click', openJobsPanel);
  document.getElementById('jobs-close-btn').addEventListener('click', closeJobsPanel);
  document.getElementById('jobs-modal').addEventListener('click', e => {
    if (e.target.id === 'jobs-modal') closeJobsPanel();
  });
  document.getElementById('jobs-refresh-btn').addEventListener('click', loadJobsPanel);
  document.getElementById('jobs-fail-list').addEventListener('click', e => {
    const btn = e.target.closest('.jobs-retry-btn');
    if (btn) retryJob(btn.dataset.jid);
  });
  document.getElementById('jobs-schedule').addEventListener('click', e => {
    const btn = e.target.closest('.jobs-sched-save-btn');
    if (btn) saveSchedJob(btn.dataset.jid);
  });

  // Settings modal
  document.getElementById('settings-close-btn').addEventListener('click', closeSettingsModal);
  document.getElementById('settings-save-btn').addEventListener('click', saveSettingsModal);
  document.getElementById('settings-modal').addEventListener('click', e => {
    if (e.target.id === 'settings-modal') closeSettingsModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeSettingsModal(); closeKeywordModal(); closeJobsPanel(); }
  });

  // BD pitch tooltip on analyzed rows
  const bdTooltip = document.getElementById('bd-tooltip');
  document.addEventListener('mousemove', e => {
    const tr = e.target.closest('tr[data-rowidx]');
    if (!tr) { bdTooltip.style.display = 'none'; return; }
    const rowIdx = parseInt(tr.dataset.rowidx, 10);
    const r = tableRows[rowIdx];
    const pitch = r && r.biz && r.biz.bd_pitch;
    if (!pitch) { bdTooltip.style.display = 'none'; return; }
    const followups = (r.score && r.score.followups) || [];
    const followupHtml = followups.length
      ? `<div class="bd-tooltip-divider"></div><div class="bd-tooltip-label">🔍 追问清单</div>` +
        followups.map((q, i) => `<div class="bd-followup">${i + 1}. ${escHtml(q)}</div>`).join('')
      : '';
    bdTooltip.innerHTML = `<div class="bd-tooltip-label">💬 BD 话术</div>${escHtml(pitch)}${followupHtml}`;
    bdTooltip.style.display = 'block';
    const tw = bdTooltip.offsetWidth, th = bdTooltip.offsetHeight;
    let x = e.clientX + 18, y = e.clientY + 14;
    if (x + tw > window.innerWidth - 8) x = e.clientX - tw - 14;
    if (y + th > window.innerHeight - 8) y = e.clientY - th - 14;
    bdTooltip.style.left = x + 'px';
    bdTooltip.style.top  = y + 'px';
  });

  init();
});
