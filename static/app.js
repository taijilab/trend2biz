/* Trend2Biz Web Dashboard — app.js */
'use strict';

const API = '/api/v1';
const POLL_INTERVAL = 1500;
const POLL_TIMEOUT = 120000;

// ── State ──────────────────────────────────────────────────────────────────

let currentDate = todayStr();
let currentSince = 'daily';
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

function mergeRows(snapshotItems, projects) {
  // Build name→project map
  const projMap = {};
  for (const p of projects) {
    projMap[p.repo_full_name] = p;
  }

  return snapshotItems.map(si => {
    const proj = projMap[si.repo_full_name] || null;
    return {
      rank: si.rank,
      repo_full_name: si.repo_full_name,
      description: si.description,
      primary_language: si.primary_language,
      stars_delta: si.stars_delta_window,
      stars_total: si.stars_total_hint,
      project_id: proj ? proj.project_id : null,
      analyzed: proj && (proj.latest_score !== null || proj.latest_biz_profile !== null),
      score: proj ? proj.latest_score : null,
      biz: proj ? proj.latest_biz_profile : null,
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

    // Step 2: biz:generate
    renderAnalysisCell(rowIdx, 'biz');
    const r2 = await apiFetch(`${API}/projects/${projectId}/biz-profiles:generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: 'rule-v1' }),
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

  if (row.analyzed && row.score) {
    const s = row.score;
    const b = row.biz;
    const grade = s.grade || '?';
    const total = typeof s.total_score === 'number' ? s.total_score.toFixed(1) : '?';
    const cat = b ? (b.category || '') : '';
    const mono = b && b.monetization_candidates ? b.monetization_candidates.slice(0, 2).join(', ') : '';
    return `
      <div class="score-info">
        <div class="score-row">
          ${gradeBadgeHtml(grade)}
          <span class="score-num">${total}</span>
        </div>
        ${cat ? `<span class="biz-category">${cat}</span>` : ''}
        ${mono ? `<span class="biz-monetization">${mono}</span>` : ''}
      </div>`;
  }

  if (!row.project_id) {
    return `<span style="color:var(--text-dim);font-size:12px">— 未入库</span>`;
  }

  return `<button class="analyze-btn" onclick="startAnalysis(${rowIdx})">&#9889; 分析</button>`;
}

function buildTableHtml(rows) {
  if (!rows.length) return '<div class="empty-state"><p>无项目数据</p></div>';

  const analysedCount = rows.filter(r => r.analyzed).length;

  let html = `
    <div class="table-summary">已分析 ${analysedCount} / ${rows.length} 个项目</div>
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

  rows.forEach((row, idx) => {
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
  el.textContent = `已分析 ${analysedCount} / ${tableRows.length} 个项目`;
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
        // Historical date with no data
        content.innerHTML = `
          <div class="empty-state">
            <h3>暂无数据</h3>
            <p>${date} 尚无 ${since} Trending 数据</p>
          </div>`;
        document.getElementById('footer-info').textContent = `${date} · ${since} · 无数据`;
        return;
      }

      // Today: offer to fetch
      if (forceRefetch) {
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

    // Fetch projects for biz/score merge
    const projects = await fetchProjects();
    tableRows = mergeRows(snapshotItems, projects);

    content.innerHTML = buildTableHtml(tableRows);
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
  const dateInput = document.getElementById('date-input');
  const sinceSelect = document.getElementById('since-select');
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnFetch = document.getElementById('btn-fetch');
  const btnRefresh = document.getElementById('btn-refresh');

  // Set initial values
  dateInput.value = currentDate;
  dateInput.max = todayStr();
  sinceSelect.value = currentSince;

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

document.addEventListener('DOMContentLoaded', init);
