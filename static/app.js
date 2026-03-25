// ─────────────────────────────────────────────────────────────
// Tab switching
// ─────────────────────────────────────────────────────────────
function switchTab(name) {
  closeDetailPanel();
  closeLibraryDetail();
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const tab = document.getElementById('tab-' + name);
  if (tab) tab.classList.add('active');
  const btn = document.querySelector(`.tab-btn[data-tab="${name}"]`);
  if (btn) btn.classList.add('active');
  if (name === 'library') loadLibrary();
  if (name === 'topics') loadTopics();
}

// ─────────────────────────────────────────────────────────────
// Log panel
// ─────────────────────────────────────────────────────────────
let _logUnread = 0;

function toggleLogPanel() {
  const panel = document.getElementById('log-panel');
  panel.classList.toggle('collapsed');
  const collapsed = panel.classList.contains('collapsed');
  document.documentElement.style.setProperty('--log-panel-height', collapsed ? '40px' : '224px');
  if (!collapsed) {
    _logUnread = 0;
    const badge = document.getElementById('log-badge');
    badge.style.display = 'none';
    badge.textContent = '0';
  }
}

function appendLog(message, level) {
  level = level || 'info';
  const body = document.getElementById('log-panel-body');
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  const prefix = `[${level.toUpperCase()}]`;
  const prefixClass = 'log-prefix-' + level;
  entry.innerHTML = `<span class="${prefixClass}">${prefix}</span> ${esc(message)}`;
  body.appendChild(entry);
  body.scrollTop = body.scrollHeight;

  const panel = document.getElementById('log-panel');
  if (panel.classList.contains('collapsed')) {
    _logUnread++;
    const badge = document.getElementById('log-badge');
    badge.textContent = _logUnread;
    badge.style.display = '';
  }
}

// ─────────────────────────────────────────────────────────────
// Detail panel
// ─────────────────────────────────────────────────────────────
function buildDetailHtml(p) {
  const allAuthors = (p.authors || []).map(a => esc(a)).join(', ');
  const catChips = (p.categories || []).map(c => `<span class="chip">${esc(c)}</span>`).join('');
  const pdfUrl = p.url ? p.url.replace('/abs/', '/pdf/') : '';
  return `
    <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:10px;line-height:1.4">${esc(p.title)}</div>
    <div class="paper-detail-authors">${allAuthors || '—'}</div>
    ${catChips ? `<div class="chips" style="margin-bottom:8px">${catChips}</div>` : ''}
    <div class="paper-detail-abstract">${esc(p.abstract || 'No abstract available.')}</div>
    <div class="paper-detail-links">
      <a href="${esc(p.url)}" target="_blank" rel="noopener">View on arXiv</a>
      ${pdfUrl ? `<a href="${esc(pdfUrl)}" target="_blank" rel="noopener">Download PDF</a>` : ''}
    </div>`;
}

function buildDiscoveryDetailHtml(p) {
  let html = '';

  // Relevance score
  if (p.relevance_score != null) {
    let scoreClass = 'score-low';
    let label = 'Low relevance';
    if (p.relevance_score >= 3) { scoreClass = 'score-high'; label = 'High relevance'; }
    else if (p.relevance_score >= 1) { scoreClass = 'score-mid'; label = 'Medium relevance'; }
    html += `
      <div class="detail-section">
        <div class="detail-section-label">Relevance</div>
        <span class="relevance-badge ${scoreClass}">${p.relevance_score.toFixed(1)}</span>
        <span style="margin-left:6px;font-size:12px;color:var(--text-dim)">${label}</span>
      </div>`;
  }

  // Topics
  const realTopics = (p._topics || []).filter(t => t !== '__other__');
  if (realTopics.length) {
    html += `
      <div class="detail-section">
        <div class="detail-section-label">Topics</div>
        <div class="chips">${realTopics.map(t => `<span class="chip chip-topic">${esc(t)}</span>`).join('')}</div>
      </div>`;
  }

  // Keywords
  if (p.extracted_keywords && p.extracted_keywords.length) {
    const matchedSet = new Set((p.matched_keywords || []).map(k => k.toLowerCase()));
    const kwChips = p.extracted_keywords.slice(0, 12).map(([kw]) => {
      const isMatch = matchedSet.has(kw.toLowerCase());
      return `<span class="chip ${isMatch ? 'chip-matched' : ''}">${esc(kw)}</span>`;
    }).join('');
    html += `
      <div class="detail-section">
        <div class="detail-section-label">Keywords</div>
        <div class="chips">${kwChips}</div>
      </div>`;
  }

  // Citation checks
  if (p.citations && p.citations.length) {
    const citeItems = p.citations.map(c => {
      const icon = c.cited ? '&#10003;' : '&#10007;';
      const cls = c.cited ? 'cite-cited' : 'cite-not-cited';
      const sections = c.cited && c.sections && c.sections.length
        ? ` <span class="cite-sections">(${c.sections.join(', ')})</span>` : '';
      return `<div class="cite-item ${cls}"><span class="cite-icon">${icon}</span> ${esc(c.title)}${sections}</div>`;
    }).join('');
    html += `
      <div class="detail-section">
        <div class="detail-section-label">Citation Checks</div>
        ${citeItems}
      </div>`;
  }

  // References (full bibliography)
  if (p.references && p.references.length) {
    const refItems = p.references.map(r => {
      const title = r.title || r.raw || '—';
      const authors = r.authors || '';
      const year = r.year ? ` (${r.year})` : '';
      const tag = r.tag ? `<span style="font-family:var(--mono);font-size:10px;color:var(--text-dim);margin-right:4px">${esc(r.tag)}</span>` : '';
      const sections = r.sections && r.sections.length
        ? ` <span class="cite-sections">${r.sections.join(', ')}</span>` : '';
      return `<div class="ref-item">${tag}${esc(authors)}${year} — ${esc(title)}${sections}</div>`;
    }).join('');
    html += `
      <div class="detail-section">
        <div class="detail-section-label">References (${p.references.length})</div>
        <div class="ref-list">${refItems}</div>
      </div>`;
  }

  // Analysis error
  if (p.analysis_error) {
    html += `
      <div class="detail-section">
        <div class="detail-section-label">Error</div>
        <div class="analysis-error">${esc(p.analysis_error)}</div>
      </div>`;
  }

  // Action buttons
  const pid = esc(p.id);
  const analyzeBtn = p.relevance_score == null
    ? `<button class="btn btn-primary" onclick="analyzeSinglePaper('${pid}', this)">Analyze</button>`
    : '';
  html += `
    <div class="detail-actions">
      ${analyzeBtn}
      <button class="btn btn-primary" onclick="saveSinglePaper('${pid}', this)">Save to Library</button>
    </div>`;

  return html;
}

function openDetailPanel(p) {
  document.getElementById('detail-panel-title').textContent = 'Paper';
  document.getElementById('detail-panel-body').innerHTML =
    buildDetailHtml(p) + buildDiscoveryDetailHtml(p);
}

function closeDetailPanel() {
  document.getElementById('detail-panel-body').innerHTML = '<div class="empty">Select a paper to view details</div>';
  document.querySelectorAll('.paper-row.active').forEach(r => r.classList.remove('active'));
}

function openLibraryDetail(p) {
  document.getElementById('lib-detail-title').textContent = 'Paper';
  const pid = esc(p.id);
  const html = buildDetailHtml(p) + `
    <div class="detail-actions">
      <button class="btn btn-primary" onclick="reanalyseLibPaper('${pid}', this)">Re-analyse</button>
      <button class="btn btn-danger" onclick="removeFromLibrary('${pid}')">Remove</button>
    </div>`;
  document.getElementById('lib-detail-body').innerHTML = html;
}

function closeLibraryDetail() {
  const body = document.getElementById('lib-detail-body');
  if (body) body.innerHTML = '<div class="empty">Select a paper to view details</div>';
  document.querySelectorAll('.library-row.active').forEach(r => r.classList.remove('active'));
}

// ─────────────────────────────────────────────────────────────
// Topics
// ─────────────────────────────────────────────────────────────
let _topicsLibraryMap = new Map();
let _selectedTopicName = null;
let _topicsCache = [];

async function loadTopics() {
  const [topicsRes, libraryRes] = await Promise.all([
    fetch('/api/topics'),
    fetch('/api/library'),
  ]);
  const topics = await topicsRes.json();
  const lib = await libraryRes.json();
  _topicsLibraryMap = new Map((lib.papers || []).map(p => [p.id, p]));
  _topicsCache = topics;
  renderTopicsList(topics);
  if (_selectedTopicName) {
    const still = topics.find(t => t.name === _selectedTopicName);
    if (still) renderTopicDetail(still);
    else { _selectedTopicName = null; renderTopicDetailEmpty(); }
  }
}

function renderTopicsList(topics) {
  const el = document.getElementById('topics-list');
  if (!topics.length) {
    el.innerHTML = '<div class="empty">No topics yet.</div>';
    return;
  }
  el.innerHTML = topics.map(t => {
    const kwCount = (t.keywords || []).length;
    const pCount = (t.papers || []).filter(p => typeof p === 'string').length;
    const active = t.name === _selectedTopicName ? ' active' : '';
    return `
      <div class="topic-row${active}" onclick="selectTopic(${esc(JSON.stringify(t.name))})">
        <span class="topic-row-name">${esc(t.name)}</span>
        <span class="topic-row-counts">
          <span class="topic-row-badge">${kwCount}kw</span>
          <span class="topic-row-badge">${pCount}p</span>
        </span>
      </div>`;
  }).join('');
}

function selectTopic(name) {
  _selectedTopicName = name;
  // Update active class on rows
  document.querySelectorAll('.topic-row').forEach(r => r.classList.remove('active'));
  const rows = document.querySelectorAll('.topic-row');
  rows.forEach(r => {
    if (r.querySelector('.topic-row-name').textContent === name) r.classList.add('active');
  });
  const t = _topicsCache.find(t => t.name === name);
  if (t) renderTopicDetail(t);
}

function renderTopicDetail(t) {
  document.getElementById('topic-detail-title').textContent = t.name;

  const assignedIds = (t.papers || []).filter(p => typeof p === 'string');
  const nameJson = esc(JSON.stringify(t.name));

  // Keywords with remove buttons + add input
  const keywordsHtml = `
    <div class="chips editable-chips">
      ${(t.keywords || []).map(k => `<span class="chip chip-removable">${esc(k)}<button class="chip-remove" onclick="removeKeywordFromTopic(${nameJson}, ${esc(JSON.stringify(k))})">&times;</button></span>`).join('')}
      <span class="chip chip-add" onclick="this.style.display='none'; this.nextElementSibling.style.display='inline-flex'; this.nextElementSibling.querySelector('input').focus()">+ Add</span>
      <span class="chip-add-input" style="display:none">
        <input type="text" placeholder="keyword" onkeydown="if(event.key==='Enter'){addKeywordToTopic(${nameJson}, this.value); this.value='';} if(event.key==='Escape'){this.parentElement.style.display='none'; this.parentElement.previousElementSibling.style.display='';}"
               onblur="if(this.value.trim()) addKeywordToTopic(${nameJson}, this.value); this.value=''; this.parentElement.style.display='none'; this.parentElement.previousElementSibling.style.display='';">
      </span>
    </div>`;

  // Description (editable)
  const descHtml = `
    <div class="topic-description">
      <textarea class="topic-desc-edit" placeholder="Add a description…"
                onblur="updateTopicDescription(${nameJson}, this.value)"
                onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault(); this.blur();}">${esc(t.description || '')}</textarea>
    </div>`;

  // Associated papers with "Get Keywords" button
  const assignedHtml = assignedIds.length ? `
    <div class="topic-papers">
      ${assignedIds.map(id => {
        const p = _topicsLibraryMap.get(id);
        const tip = p ? esc(p.title) : esc(id);
        return `<div class="topic-paper-item">
          <span title="${tip}">${p ? esc(p.title) : esc(id)}</span>
          <button class="btn btn-sm" style="padding:1px 8px;font-size:11px;background:var(--surface2);border:1px solid var(--border);color:var(--accent)"
                  onclick="extractPaperKeywords(${nameJson}, ${esc(JSON.stringify(id))}, this)">Get Keywords</button>
          <button class="btn btn-danger" style="padding:1px 6px;font-size:11px"
                  onclick="removePaperFromTopic(${nameJson}, ${esc(JSON.stringify(id))})">×</button>
        </div>`;
      }).join('')}
    </div>` : '';

  const available = [..._topicsLibraryMap.values()].filter(p => !assignedIds.includes(p.id));
  const panelInner = available.length
    ? available.map(p => {
        const searchText = ((p.title || '') + ' ' + (p.authors || []).join(' ')).toLowerCase();
        return `
        <div class="lib-panel-item" data-search="${esc(searchText)}">
          <span title="${esc(p.title)}">${esc(p.title)}</span>
          <button class="btn btn-primary" style="padding:2px 8px;font-size:11px;flex-shrink:0"
                  onclick="addPaperToTopic(${nameJson}, ${esc(JSON.stringify(p.id))})">Add</button>
        </div>`;
      }).join('')
    : `<div style="color:var(--text-dim);font-size:12px;padding:4px 0">${_topicsLibraryMap.size ? 'All library papers already assigned.' : 'No papers in library yet.'}</div>`;

  document.getElementById('topic-detail-body').innerHTML = `
    ${descHtml}
    ${keywordsHtml}
    ${assignedHtml}
    <div class="keyword-suggestions" id="topic-kw-suggestions" style="display:none"></div>
    <div style="margin-top:8px">
      <button class="btn" style="background:var(--surface2);border:1px solid var(--border);color:var(--text-dim);font-size:12px;padding:4px 10px"
              onclick="toggleLibPanel('topic-lib-panel', this)">+ Add from Library</button>
      <div class="lib-panel" id="topic-lib-panel" style="display:none">
        <input type="text" class="lib-panel-search" placeholder="Search papers…"
               oninput="filterLibPanel(this)" onclick="event.stopPropagation()"
               style="margin-bottom:6px">
        <div class="lib-panel-items">
          ${panelInner}
        </div>
      </div>
    </div>
    <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
      <button class="btn btn-danger" onclick="deleteTopic(${nameJson})">Delete Topic</button>
    </div>`;
}

function renderTopicDetailEmpty() {
  document.getElementById('topic-detail-title').textContent = 'Topic';
  document.getElementById('topic-detail-body').innerHTML = '<div class="empty">Select a topic to view details</div>';
}

async function updateTopicDescription(topicName, description) {
  await fetch('/api/topics/' + encodeURIComponent(topicName), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ description }),
  });
}

async function addKeywordToTopic(topicName, keyword) {
  keyword = keyword.trim();
  if (!keyword) return;
  const res = await fetch('/api/topics');
  const topics = await res.json();
  const topic = topics.find(t => t.name === topicName);
  if (!topic) return;
  const keywords = [...(topic.keywords || [])];
  if (keywords.some(k => k.toLowerCase() === keyword.toLowerCase())) return;
  keywords.push(keyword);
  await fetch('/api/topics/' + encodeURIComponent(topicName), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ keywords }),
  });
  loadTopics();
}

async function removeKeywordFromTopic(topicName, keyword) {
  const res = await fetch('/api/topics');
  const topics = await res.json();
  const topic = topics.find(t => t.name === topicName);
  if (!topic) return;
  const keywords = (topic.keywords || []).filter(k => k !== keyword);
  await fetch('/api/topics/' + encodeURIComponent(topicName), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ keywords }),
  });
  loadTopics();
}

async function extractPaperKeywords(topicName, paperId, btn) {
  const origText = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:4px"></span>…';

  try {
    const res = await fetch('/api/topics/' + encodeURIComponent(topicName) + '/extract-keywords', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ paper_id: paperId }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Failed to extract keywords');
      return;
    }

    // Show suggestions in detail panel
    let sugEl = document.getElementById('topic-kw-suggestions');
    if (!sugEl) {
      sugEl = document.createElement('div');
      sugEl.className = 'keyword-suggestions';
      sugEl.id = 'topic-kw-suggestions';
      document.getElementById('topic-detail-body').appendChild(sugEl);
    }
    sugEl.style.display = 'block';

    const keywords = data.keywords || [];
    sugEl.innerHTML = `
      <div class="kw-sug-header">Extracted keywords <button class="kw-sug-close" onclick="this.closest('.keyword-suggestions').style.display='none'">&times;</button></div>
      <div class="kw-sug-chips">
        ${keywords.map(([kw, score]) =>
          `<span class="chip chip-suggestion" onclick="addKeywordToTopic(${esc(JSON.stringify(topicName))}, ${esc(JSON.stringify(kw))}); this.classList.add('chip-added'); this.onclick=null;">${esc(kw)} <span class="kw-score">${score.toFixed(2)}</span></span>`
        ).join('')}
      </div>`;
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

function toggleLibPanel(panelId, btn) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const opening = panel.style.display === 'none';
  panel.style.display = opening ? 'block' : 'none';
  btn.textContent = opening ? '− Add from Library' : '+ Add from Library';
  if (opening) {
    const searchInput = panel.querySelector('.lib-panel-search');
    if (searchInput) {
      searchInput.value = '';
      panel.querySelectorAll('.lib-panel-item').forEach(item => item.style.display = '');
    }
  }
}

function filterLibPanel(input) {
  const q = input.value.toLowerCase();
  input.closest('.lib-panel').querySelectorAll('.lib-panel-item').forEach(item => {
    item.style.display = item.dataset.search.includes(q) ? '' : 'none';
  });
}

async function addPaperToTopic(topicName, paperId) {
  const res = await fetch('/api/topics/' + encodeURIComponent(topicName) + '/papers', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ paper_id: paperId }),
  });
  if (res.ok) loadTopics();
}

async function removePaperFromTopic(topicName, paperId) {
  const res = await fetch(
    '/api/topics/' + encodeURIComponent(topicName) + '/papers/' + encodeURIComponent(paperId),
    { method: 'DELETE' }
  );
  if (res.ok) loadTopics();
}

async function addTopic() {
  const name = document.getElementById('new-topic-name').value.trim();
  const raw = document.getElementById('new-topic-keywords').value;
  const keywords = raw.split(',').map(k => k.trim()).filter(Boolean);
  const errEl = document.getElementById('add-error');
  errEl.innerHTML = '';

  if (!name) { errEl.innerHTML = '<div class="error-msg">Name is required.</div>'; return; }

  const res = await fetch('/api/topics', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ name, keywords }),
  });

  if (!res.ok) {
    const data = await res.json();
    errEl.innerHTML = `<div class="error-msg">${esc(data.error || 'Error adding topic')}</div>`;
    return;
  }

  document.getElementById('new-topic-name').value = '';
  document.getElementById('new-topic-keywords').value = '';
  _selectedTopicName = name;
  loadTopics();
}

async function deleteTopic(name) {
  const res = await fetch('/api/topics/' + encodeURIComponent(name), { method: 'DELETE' });
  if (res.ok) {
    if (_selectedTopicName === name) {
      _selectedTopicName = null;
      renderTopicDetailEmpty();
    }
    loadTopics();
  }
}

// ─────────────────────────────────────────────────────────────
// Papers
// ─────────────────────────────────────────────────────────────
async function fetchPapers() {
  const categories = document.getElementById('fetch-cats').value.trim().split(/\s+/).filter(Boolean);
  const date_from = document.getElementById('fetch-from').value || null;
  const date_to = document.getElementById('fetch-to').value || null;
  const max_results = parseInt(document.getElementById('fetch-max').value) || 50;
  const errEl = document.getElementById('fetch-error');
  const statusEl = document.getElementById('fetch-status');
  const btn = document.getElementById('fetch-btn');
  errEl.innerHTML = '';

  if (!categories.length) {
    errEl.innerHTML = '<div class="error-msg">Enter at least one category.</div>';
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Fetching…';
  statusEl.textContent = '';
  document.getElementById('fetch-results').innerHTML = '';
  closeDetailPanel();

  appendLog(`Fetching papers: ${categories.join(', ')}`, 'info');

  try {
    const res = await fetch('/api/fetch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ categories, date_from, date_to, max_results }),
    });

    const data = await res.json();

    if (!res.ok) {
      errEl.innerHTML = `<div class="error-msg">${esc(data.error || 'Fetch failed')}</div>`;
      appendLog(data.error || 'Fetch failed', 'error');
      return;
    }

    renderResults(data);
    statusEl.textContent = `Done — ${data.total_fetched} fetched, ${data.total_matched} matched`;
    appendLog(`Fetched ${data.total_fetched} papers, ${data.total_matched} matched`, 'ok');
  } catch (e) {
    errEl.innerHTML = `<div class="error-msg">Request failed: ${esc(e.message)}</div>`;
    appendLog(`Fetch error: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Fetch &amp; Filter';
  }
}

// paper data store: id → full paper object (populated on each render)
const paperStore = new Map();

// pagination state: groupId → { papers: paper[], page: number }
const groupPapers = new Map();
const PAPERS_PER_PAGE = 10;

function buildPaperRowHtml(p) {
  const authors = formatAuthors(p.authors);
  const date = (p.published || '').slice(0, 10);
  const pid = esc(p.id);
  const analyzed = p.relevance_score != null;

  let titleHtml;
  if (analyzed) {
    let scoreClass = 'score-low';
    if (p.relevance_score >= 3) scoreClass = 'score-high';
    else if (p.relevance_score >= 1) scoreClass = 'score-mid';
    titleHtml = `<div style="display:flex;align-items:center;gap:8px">
              <span class="relevance-badge ${scoreClass}">${p.relevance_score.toFixed(1)}</span>
              <div class="paper-title">${esc(p.title)}</div>
            </div>`;
  } else {
    titleHtml = `<div class="paper-title">${esc(p.title)}</div>`;
  }

  return `
      <div class="paper-row" onclick="selectPaperRow(this, '${pid}', 'paper')">
        <div style="padding:10px 16px; display:flex; gap:10px; align-items:flex-start">
          <input type="checkbox" class="paper-select" data-id="${pid}"
                 onclick="event.stopPropagation(); updateSaveBar()">
          <div style="flex:1">
            ${titleHtml}
            <div class="paper-meta">${esc(authors)} &nbsp;·&nbsp; ${esc(date)}</div>
          </div>
        </div>
      </div>`;
}

function renderGroupPage(groupId, page) {
  const gp = groupPapers.get(groupId);
  if (!gp) return;

  // Remember which papers are checked before re-render
  const groupBody = document.getElementById(groupId);
  const checkedIds = new Set();
  if (groupBody) {
    groupBody.querySelectorAll('.paper-select:checked').forEach(cb => checkedIds.add(cb.dataset.id));
  }

  const totalPages = Math.max(1, Math.ceil(gp.papers.length / PAPERS_PER_PAGE));
  page = Math.max(0, Math.min(page, totalPages - 1));
  gp.page = page;

  const start = page * PAPERS_PER_PAGE;
  const slice = gp.papers.slice(start, start + PAPERS_PER_PAGE);

  let html = '';
  for (const p of slice) {
    html += buildPaperRowHtml(p);
  }

  groupBody.innerHTML = html;

  // Update pagination controls in the header
  const pgEl = document.getElementById('pg-' + groupId);
  if (pgEl) {
    if (totalPages > 1) {
      pgEl.innerHTML = `
        <button class="pg-btn" ${page === 0 ? 'disabled' : ''} onclick="renderGroupPage('${groupId}', ${page - 1})">‹</button>
        <span class="pg-info">${page + 1} / ${totalPages}</span>
        <button class="pg-btn" ${page >= totalPages - 1 ? 'disabled' : ''} onclick="renderGroupPage('${groupId}', ${page + 1})">›</button>`;
    } else {
      pgEl.innerHTML = '';
    }
  }

  // Restore checkbox state
  checkedIds.forEach(id => {
    const cb = groupBody.querySelector(`.paper-select[data-id="${CSS.escape(id)}"]`);
    if (cb) cb.checked = true;
  });
  updateSaveBar();
}

function renderResults(data) {
  const middleEl = document.getElementById('fetch-results');
  const leftEl = document.getElementById('disc-left');

  if (!data.results || !data.results.length) {
    leftEl.innerHTML = '';
    middleEl.innerHTML = `
      <div class="summary">
        <strong>${data.total_fetched}</strong> papers fetched — <strong style="color:var(--text-dim)">0</strong> matched your topics.
      </div>
      <div class="empty">No papers matched your topics.</div>`;
    closeDetailPanel();
    return;
  }

  // Populate paper store (tag each paper with its topic memberships)
  paperStore.clear();
  for (const group of data.results) {
    for (const p of group.papers) {
      if (paperStore.has(p.id)) {
        paperStore.get(p.id)._topics.push(group.topic);
      } else {
        p._topics = [group.topic];
        paperStore.set(p.id, p);
      }
    }
  }

  const topicCount = data.results.filter(g => g.topic !== '__other__').length;

  // ── Left sidebar ──
  let sidebarHtml = `<h3>Filter topics</h3>`;
  for (const group of data.results) {
    const label = group.topic === '__other__' ? 'Other' : group.topic;
    const gid = 'g-' + group.topic.replace(/\W+/g, '_');
    sidebarHtml += `
      <div class="sidebar-topic">
        <label title="${esc(label)}">
          <input type="checkbox" checked data-topic="${esc(group.topic)}"
                 onchange="filterTopic(this)">
          ${esc(label)} <span style="color:var(--text-dim)">(${group.match_count})</span>
        </label>
      </div>`;
  }
  sidebarHtml += `
    <div class="sidebar-actions">
      <button onclick="setAllTopics(true)">All</button>
      <button onclick="setAllTopics(false)">None</button>
    </div>`;
  leftEl.innerHTML = sidebarHtml;

  // ── Middle column ──
  let groupsHtml = `
    <div class="summary">
      <strong>${data.total_fetched}</strong> papers fetched &nbsp;·&nbsp;
      <strong>${data.total_matched}</strong> matched across
      <strong>${topicCount}</strong> topic${topicCount !== 1 ? 's' : ''}
    </div>`;

  groupPapers.clear();

  for (const group of data.results) {
    const gid = 'g-' + group.topic.replace(/\W+/g, '_');
    const groupLabel = group.topic === '__other__' ? 'Other (unmatched)' : group.topic;
    groupPapers.set(gid, { papers: group.papers, page: 0 });
    groupsHtml += `
      <div class="result-group" data-topic="${esc(group.topic)}">
        <div class="result-group-header open" onclick="toggleGroup('${gid}', this)">
          ${esc(groupLabel)}
          <span class="count">${group.match_count}</span>
          <span class="group-pagination" id="pg-${gid}" onclick="event.stopPropagation()"></span>
          <span class="chevron">▶</span>
        </div>
        <div class="result-group-body open" id="${gid}"></div>
      </div>`;
  }

  middleEl.innerHTML = groupsHtml;

  // Render first page of each group
  for (const gid of groupPapers.keys()) {
    renderGroupPage(gid, 0);
  }

  // Reset detail panel
  closeDetailPanel();
}

function selectPaperRow(row, paperId, store) {
  if (row.classList.contains('active')) {
    if (store === 'library') closeLibraryDetail();
    else closeDetailPanel();
    return;
  }
  if (store === 'library') {
    document.querySelectorAll('.library-row.active').forEach(r => r.classList.remove('active'));
    row.classList.add('active');
    const p = libraryStore.get(paperId);
    if (p) openLibraryDetail(p);
  } else {
    document.querySelectorAll('.paper-row.active').forEach(r => r.classList.remove('active'));
    row.classList.add('active');
    const p = paperStore.get(paperId);
    if (p) openDetailPanel(p);
  }
}

function toggleGroup(id, header) {
  header.classList.toggle('open');
  document.getElementById(id).classList.toggle('open');
}

function filterTopic(checkbox) {
  const topic = checkbox.dataset.topic;
  const group = document.querySelector(`.result-group[data-topic="${CSS.escape(topic)}"]`);
  if (group) group.style.display = checkbox.checked ? '' : 'none';
}

function setAllTopics(checked) {
  document.querySelectorAll('#disc-left input[type=checkbox]').forEach(cb => {
    cb.checked = checked;
    filterTopic(cb);
  });
}

function renderAnalyzedGroup(groupId, analyzedPapers) {
  // Merge analysis results back into paperStore
  for (const p of analyzedPapers) {
    const orig = paperStore.get(p.id);
    if (orig) {
      orig.relevance_score = p.relevance_score;
      orig.extracted_keywords = p.extracted_keywords;
      orig.matched_keywords = p.matched_keywords;
      orig.citations = p.citations;
      orig.references = p.references;
      orig.analysis_error = p.error;
    }
  }

  // Update groupPapers with enriched paper objects from paperStore
  const gp = groupPapers.get(groupId);
  if (gp) {
    gp.papers = gp.papers.map(p => paperStore.get(p.id) || p);
    renderGroupPage(groupId, gp.page);
  }

  // Refresh detail panel if the active paper was just analyzed
  const activeRow = document.querySelector('#disc-middle .paper-row.active');
  if (activeRow) {
    const activeId = activeRow.querySelector('.paper-select')?.dataset?.id;
    if (activeId && paperStore.has(activeId)) openDetailPanel(paperStore.get(activeId));
  }
}

// ─────────────────────────────────────────────────────────────
// Save bar
// ─────────────────────────────────────────────────────────────
function updateSaveBar() {
  const checked = document.querySelectorAll('.paper-select:checked');
  const bar = document.getElementById('save-bar');
  const countEl = document.getElementById('save-count');
  if (checked.length > 0) {
    bar.style.display = 'flex';
    countEl.textContent = `${checked.length} selected`;
    document.documentElement.style.setProperty('--save-bar-height', '50px');
  } else {
    bar.style.display = 'none';
    document.documentElement.style.setProperty('--save-bar-height', '0px');
  }
}

function clearSelection() {
  document.querySelectorAll('.paper-select:checked').forEach(cb => { cb.checked = false; });
  updateSaveBar();
}

async function saveSinglePaper(paperId, btn) {
  const p = paperStore.get(paperId);
  if (!p) return;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:4px"></span>Saving…';
  try {
    const res = await fetch('/api/library', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ papers: [p] }),
    });
    if (res.ok) {
      btn.textContent = 'Saved';
      appendLog(`Saved "${p.title}" to library`, 'ok');
    } else {
      btn.disabled = false;
      btn.textContent = 'Save to Library';
    }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Save to Library';
    appendLog('Save failed: ' + e.message, 'error');
  }
}

async function analyzeSinglePaper(paperId, btn) {
  const p = paperStore.get(paperId);
  if (!p) return;

  // Pick first real topic for context
  const topicName = (p._topics || []).find(t => t !== '__other__') || '';

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:4px"></span>Analyzing…';
  appendLog(`Analyzing "${p.title}"`, 'info');

  try {
    const res = await fetch('/api/analyze-paper', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        paper_id: p.id,
        title: p.title,
        authors: p.authors,
        abstract: p.abstract,
        topic_name: topicName,
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      btn.disabled = false;
      btn.textContent = 'Analyze';
      appendLog(`Analysis failed: ${data.error}`, 'error');
      return;
    }

    // Merge results into paperStore
    p.relevance_score = data.relevance_score;
    p.extracted_keywords = data.extracted_keywords;
    p.matched_keywords = data.matched_keywords;
    p.citations = data.citations;
    p.references = data.references;
    p.analysis_error = data.error || null;

    appendLog(`Analysis done for "${p.title}" — score: ${data.relevance_score}`, 'ok');

    // Refresh detail panel
    openDetailPanel(p);

    // Re-render the group page this paper belongs to
    for (const [gid, gp] of groupPapers) {
      if (gp.papers.some(gpp => gpp.id === paperId)) {
        gp.papers = gp.papers.map(gpp => paperStore.get(gpp.id) || gpp);
        renderGroupPage(gid, gp.page);
      }
    }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Analyze';
    appendLog(`Analysis error: ${e.message}`, 'error');
  }
}

async function analyzeSelected() {
  const checked = [...document.querySelectorAll('.paper-select:checked')];
  const papers = checked.map(cb => paperStore.get(cb.dataset.id)).filter(Boolean);
  if (!papers.length) return;

  const btn = document.getElementById('analyze-sel-btn');
  const countEl = document.getElementById('save-count');
  const total = papers.length;
  btn.disabled = true;

  appendLog(`Analyzing ${total} selected papers`, 'info');

  for (let i = 0; i < papers.length; i++) {
    const p = papers[i];
    countEl.textContent = `Analyzing ${i + 1}/${total}…`;
    const topicName = (p._topics || []).find(t => t !== '__other__') || '';

    try {
      const res = await fetch('/api/analyze-paper', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          paper_id: p.id,
          title: p.title,
          authors: p.authors,
          abstract: p.abstract,
          topic_name: topicName,
        }),
      });

      const data = await res.json();
      if (res.ok) {
        p.relevance_score = data.relevance_score;
        p.extracted_keywords = data.extracted_keywords;
        p.matched_keywords = data.matched_keywords;
        p.citations = data.citations;
        p.references = data.references;
        p.analysis_error = data.error || null;
        appendLog(`[${i + 1}/${total}] ${p.title} — score: ${data.relevance_score}`, 'ok');
      } else {
        appendLog(`[${i + 1}/${total}] ${p.id}: ${data.error}`, 'error');
      }
    } catch (e) {
      appendLog(`[${i + 1}/${total}] ${p.id}: ${e.message}`, 'error');
    }

    // Re-render group pages this paper belongs to
    for (const [gid, gp] of groupPapers) {
      if (gp.papers.some(gpp => gpp.id === p.id)) {
        gp.papers = gp.papers.map(gpp => paperStore.get(gpp.id) || gpp);
        renderGroupPage(gid, gp.page);
      }
    }
  }

  btn.disabled = false;
  updateSaveBar();
  appendLog(`Analysis complete (${total} papers)`, 'ok');

  // Refresh detail panel if active paper was analyzed
  const activeRow = document.querySelector('#disc-middle .paper-row.active');
  if (activeRow) {
    const activeId = activeRow.querySelector('.paper-select')?.dataset?.id;
    if (activeId && paperStore.has(activeId)) openDetailPanel(paperStore.get(activeId));
  }
}

async function saveSelected() {
  const checked = [...document.querySelectorAll('.paper-select:checked')];
  const papers = checked.map(cb => paperStore.get(cb.dataset.id)).filter(Boolean);
  if (!papers.length) return;

  const res = await fetch('/api/library', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ papers }),
  });
  const data = await res.json();

  clearSelection();
  const statusEl = document.getElementById('fetch-status');
  statusEl.textContent = `Saved ${data.added} paper${data.added !== 1 ? 's' : ''} (library: ${data.total})`;
}

// ─────────────────────────────────────────────────────────────
// Library
// ─────────────────────────────────────────────────────────────
const libraryStore = new Map();
let _libTopics = [];

async function loadLibrary() {
  const [libRes, topicsRes] = await Promise.all([
    fetch('/api/library'),
    fetch('/api/topics'),
  ]);
  const libData = await libRes.json();
  const topics = await topicsRes.json();
  renderLibrary(libData.papers || [], topics);
}

function renderLibrary(papers, topics) {
  const leftEl = document.getElementById('lib-left');
  const summaryEl = document.getElementById('lib-summary');
  const resultsEl = document.getElementById('lib-results');

  _libTopics = topics;

  libraryStore.clear();
  for (const p of papers) libraryStore.set(p.id, p);

  // Summary bar
  const topicCount = topics.length;
  summaryEl.innerHTML = `<div class="summary"><strong>${papers.length}</strong> saved paper${papers.length !== 1 ? 's' : ''} · ${topicCount} topic${topicCount !== 1 ? 's' : ''}</div>`;

  if (!papers.length) {
    leftEl.innerHTML = '';
    resultsEl.innerHTML = '<div class="empty">No papers saved yet.</div>';
    closeLibraryDetail();
    return;
  }

  // Build topic → paper groups
  const libIds = new Set(papers.map(p => p.id));
  const assignedIds = new Set();
  const groups = [];

  for (const t of topics) {
    const topicPaperIds = (t.papers || []).filter(id => typeof id === 'string' && libIds.has(id));
    if (!topicPaperIds.length) continue;
    topicPaperIds.forEach(id => assignedIds.add(id));
    groups.push({ name: t.name, paperIds: topicPaperIds });
  }

  // Unassigned papers
  const unassigned = papers.filter(p => !assignedIds.has(p.id));
  if (unassigned.length) {
    groups.push({ name: '__unassigned__', paperIds: unassigned.map(p => p.id) });
  }

  // Render sidebar
  let sideHtml = '<h3>Topics</h3>';
  for (const g of groups) {
    const label = g.name === '__unassigned__' ? 'Unassigned' : g.name;
    const sanitized = g.name.replace(/[^a-zA-Z0-9_-]/g, '_');
    sideHtml += `
      <div class="sidebar-topic">
        <label>
          <input type="checkbox" checked data-lib-topic="${esc(g.name)}"
                 onchange="filterLibTopic(this)">
          ${esc(label)} <span class="count" style="margin-left:auto">${g.paperIds.length}</span>
        </label>
      </div>`;
  }
  sideHtml += `
    <div class="sidebar-actions">
      <button onclick="setAllLibTopics(true)">All</button>
      <button onclick="setAllLibTopics(false)">None</button>
    </div>`;
  leftEl.innerHTML = sideHtml;

  // Render result groups
  let groupsHtml = '';
  for (const g of groups) {
    const label = g.name === '__unassigned__' ? 'Unassigned' : g.name;
    const sanitized = g.name.replace(/[^a-zA-Z0-9_-]/g, '_');
    const groupId = 'lib-g-' + sanitized;

    groupsHtml += `<div class="result-group" data-lib-topic="${esc(g.name)}">`;
    groupsHtml += `
      <div class="result-group-header open" onclick="toggleGroup('${groupId}', this)">
        <span class="chevron">&#9654;</span>
        ${esc(label)}
        <span class="count">${g.paperIds.length}</span>
      </div>`;
    groupsHtml += `<div class="result-group-body open" id="${groupId}">`;

    for (const pid of g.paperIds) {
      const p = libraryStore.get(pid);
      if (!p) continue;
      const authors = formatAuthors(p.authors);
      const date = (p.published || '').slice(0, 10);
      const saved = (p.saved_at || '').slice(0, 10);
      const escapedId = esc(p.id);

      // Analysis results if available
      let scoreHtml = '';
      if (p.relevance_score != null) {
        let scoreClass = 'score-low';
        if (p.relevance_score >= 3) scoreClass = 'score-high';
        else if (p.relevance_score >= 1) scoreClass = 'score-mid';
        scoreHtml = `<span class="relevance-badge ${scoreClass}">${p.relevance_score.toFixed(1)}</span>`;
      }

      let kwChips = '';
      if (p.extracted_keywords) {
        const matchedSet = new Set((p.matched_keywords || []).map(k => k.toLowerCase()));
        kwChips = (p.extracted_keywords || []).slice(0, 8).map(([kw]) => {
          const isMatch = matchedSet.has(kw.toLowerCase());
          return `<span class="chip ${isMatch ? 'chip-matched' : ''}">${esc(kw)}</span>`;
        }).join('');
      }

      groupsHtml += `
        <div class="library-row" onclick="selectPaperRow(this, '${escapedId}', 'library')">
          <div style="padding:10px 16px; display:flex; gap:10px; align-items:flex-start">
            <div style="flex:1">
              <div style="display:flex;align-items:center;gap:8px">
                ${scoreHtml}
                <div class="paper-title">${esc(p.title)}</div>
              </div>
              <div class="paper-meta">${esc(authors)} &nbsp;·&nbsp; ${esc(date)}
                &nbsp;·&nbsp; <span style="font-family:var(--mono);font-size:10px;color:var(--text-dim)">${escapedId}</span>
                ${saved ? `&nbsp;·&nbsp; <span style="color:var(--text-dim)">saved ${esc(saved)}</span>` : ''}
              </div>
              ${kwChips ? `<div class="paper-chips chips">${kwChips}</div>` : ''}
            </div>
          </div>
        </div>`;
    }

    groupsHtml += `</div></div>`;
  }
  resultsEl.innerHTML = groupsHtml;

  closeLibraryDetail();
}

function filterLibTopic(checkbox) {
  const topic = checkbox.dataset.libTopic;
  const groups = document.querySelectorAll(`#lib-results .result-group[data-lib-topic="${CSS.escape(topic)}"]`);
  groups.forEach(g => g.style.display = checkbox.checked ? '' : 'none');
}

function setAllLibTopics(checked) {
  document.querySelectorAll('#lib-left input[type=checkbox]').forEach(cb => {
    cb.checked = checked;
    filterLibTopic(cb);
  });
}

async function reanalyseLibPaper(paperId, btn) {
  const p = libraryStore.get(paperId);
  if (!p) return;

  // Find the paper's first assigned topic
  let topicName = null;
  for (const t of _libTopics) {
    if ((t.papers || []).includes(paperId)) {
      topicName = t.name;
      break;
    }
  }

  if (!topicName) {
    appendLog('Paper not assigned to any topic — assign it first to enable analysis.', 'error');
    return;
  }

  const origText = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:4px"></span>Analysing…';
  appendLog(`Re-analysing "${p.title}" (topic: ${topicName})`, 'info');

  try {
    const res = await fetch('/api/analyze-topic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic_name: topicName,
        papers: [{ id: p.id, title: p.title, authors: p.authors, abstract: p.abstract }],
      }),
    });

    if (!res.ok) {
      const data = await res.json();
      appendLog('Analysis failed: ' + (data.error || 'unknown error'), 'error');
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch { continue; }

        if (evt.type === 'progress') {
          let msg = '';
          if (evt.step === 'downloading') msg = `Downloading ${evt.paper_id}…`;
          else if (evt.step === 'analyzing') msg = `Analyzing ${evt.paper_id}…`;
          else if (evt.step === 'done') msg = `Done — score: ${evt.score}`;
          if (msg) appendLog(msg, evt.step === 'done' ? 'ok' : 'info');
        } else if (evt.type === 'error') {
          appendLog(`Error (${evt.paper_id}): ${evt.message}`, 'error');
        } else if (evt.type === 'complete') {
          // Update libraryStore with analysis results
          for (const ap of evt.papers) {
            const orig = libraryStore.get(ap.id);
            if (orig) {
              orig.relevance_score = ap.relevance_score;
              orig.extracted_keywords = ap.extracted_keywords;
              orig.matched_keywords = ap.matched_keywords;
              orig.citations = ap.citations;
              orig.analysis_error = ap.error;
            }
          }
          appendLog(`Re-analysis complete for "${p.title}"`, 'ok');
          // Refresh detail panel
          const updated = libraryStore.get(paperId);
          if (updated) openLibraryDetail(updated);
        }
      }
    }
  } catch (e) {
    appendLog('Analysis error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

async function removeFromLibrary(paperId) {
  const res = await fetch('/api/library/' + encodeURIComponent(paperId), { method: 'DELETE' });
  if (res.ok) {
    closeLibraryDetail();
    loadLibrary();
  }
}

// ─────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatAuthors(authors) {
  if (!authors || !authors.length) return '';
  if (authors.length <= 3) return authors.join(', ');
  return authors.slice(0, 3).join(', ') + ' et al.';
}

// ─────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────

// Tab click listeners
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// ─────────────────────────────────────────────────────────────
// Tmp cleanup
// ─────────────────────────────────────────────────────────────
async function cleanTmp(btn) {
  btn.disabled = true;
  try {
    const res = await fetch('/api/tmp', { method: 'DELETE' });
    const data = await res.json();
    appendLog(data.message || 'Tmp cleaned', 'ok');
  } catch (e) {
    appendLog('Clean tmp failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

// Set sensible default date range (last 30 days)
(function () {
  const today = new Date();
  const prior = new Date(today);
  prior.setDate(prior.getDate() - 30);
  const fmt = d => d.toISOString().slice(0, 10);
  document.getElementById('fetch-to').value = fmt(today);
  document.getElementById('fetch-from').value = fmt(prior);
})();

loadTopics();
