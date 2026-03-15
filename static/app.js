// ─────────────────────────────────────────────────────────────
// Tab switching
// ─────────────────────────────────────────────────────────────
function switchTab(name, btn) {
  closeDetailPanel();
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  if (name === 'library') loadLibrary();
  if (name === 'topics') loadTopics();
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

function openDetailPanel(p, containerId) {
  const panel = document.getElementById('detail-panel');
  const container = document.getElementById(containerId);
  if (panel.parentElement !== container) container.appendChild(panel);
  document.getElementById('detail-panel-title').textContent = 'Paper';
  document.getElementById('detail-panel-body').innerHTML = buildDetailHtml(p);
  panel.classList.add('open');
}

function closeDetailPanel() {
  document.getElementById('detail-panel').classList.remove('open');
  document.querySelectorAll('.paper-row.active, .library-row.active').forEach(r => r.classList.remove('active'));
}

// ─────────────────────────────────────────────────────────────
// Topics
// ─────────────────────────────────────────────────────────────
let _topicsLibraryMap = new Map();

async function loadTopics() {
  const [topicsRes, libraryRes] = await Promise.all([
    fetch('/api/topics'),
    fetch('/api/library'),
  ]);
  const topics = await topicsRes.json();
  const lib = await libraryRes.json();
  _topicsLibraryMap = new Map((lib.papers || []).map(p => [p.id, p]));
  renderTopics(topics);
}

function renderTopics(topics) {
  const el = document.getElementById('topics-list');
  if (!topics.length) {
    el.innerHTML = '<div class="empty">No topics yet. Add one below.</div>';
    return;
  }
  el.innerHTML = topics.map((t, idx) => {
    const assignedIds = (t.papers || []).filter(p => typeof p === 'string');

    const assignedHtml = assignedIds.length ? `
      <div class="topic-papers">
        ${assignedIds.map(id => {
          const p = _topicsLibraryMap.get(id);
          const tip = p ? esc(p.title) : esc(id);
          return `<div class="topic-paper-item">
            <span title="${tip}">${p ? esc(p.title) : esc(id)}</span>
            <button class="btn btn-danger" style="padding:1px 6px;font-size:11px"
                    onclick="removePaperFromTopic(${esc(JSON.stringify(t.name))}, ${esc(JSON.stringify(id))})">×</button>
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
                    onclick="addPaperToTopic(${esc(JSON.stringify(t.name))}, ${esc(JSON.stringify(p.id))})">Add</button>
          </div>`;
        }).join('')
      : `<div style="color:var(--text-dim);font-size:12px;padding:4px 0">${_topicsLibraryMap.size ? 'All library papers already assigned.' : 'No papers in library yet.'}</div>`;

    return `
    <div class="card">
      <div class="card-header">
        <span class="topic-name">${esc(t.name)}</span>
        <button class="btn btn-danger" onclick="deleteTopic(${esc(JSON.stringify(t.name))})">Remove</button>
      </div>
      <div class="chips">
        ${(t.keywords || []).map(k => `<span class="chip">${esc(k)}</span>`).join('')}
      </div>
      ${assignedHtml}
      <div style="margin-top:8px">
        <button class="btn" style="background:var(--surface2);border:1px solid var(--border);color:var(--text-dim);font-size:12px;padding:4px 10px"
                onclick="toggleLibPanel('lib-panel-${idx}', this)">+ Add from Library</button>
        <div class="lib-panel" id="lib-panel-${idx}" style="display:none">
          <input type="text" class="lib-panel-search" placeholder="Search papers…"
                 oninput="filterLibPanel(this)" onclick="event.stopPropagation()"
                 style="margin-bottom:6px">
          <div class="lib-panel-items">
            ${panelInner}
          </div>
        </div>
      </div>
    </div>`;
  }).join('');
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
  loadTopics();
}

async function deleteTopic(name) {
  const res = await fetch('/api/topics/' + encodeURIComponent(name), { method: 'DELETE' });
  if (res.ok) loadTopics();
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

  try {
    const res = await fetch('/api/fetch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ categories, date_from, date_to, max_results }),
    });

    const data = await res.json();

    if (!res.ok) {
      errEl.innerHTML = `<div class="error-msg">${esc(data.error || 'Fetch failed')}</div>`;
      return;
    }

    renderResults(data);
    statusEl.textContent = `Done — ${data.total_fetched} fetched, ${data.total_matched} matched`;
  } catch (e) {
    errEl.innerHTML = `<div class="error-msg">Request failed: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Fetch &amp; Filter';
  }
}

// paper data store: id → full paper object (populated on each render)
const paperStore = new Map();

function renderResults(data) {
  const el = document.getElementById('fetch-results');

  if (!data.results || !data.results.length) {
    el.innerHTML = `
      <div class="summary">
        <strong>${data.total_fetched}</strong> papers fetched — <strong style="color:var(--text-dim)">0</strong> matched your topics.
      </div>
      <div class="empty">No papers matched your topics.</div>`;
    return;
  }

  // Populate paper store
  paperStore.clear();
  for (const group of data.results) {
    for (const p of group.papers) paperStore.set(p.id, p);
  }

  const topicCount = data.results.filter(g => g.topic !== '__other__').length;

  // Build sidebar HTML
  let sidebarHtml = `
    <div class="topic-sidebar">
      <h3>Filter topics</h3>`;
  for (const group of data.results) {
    const label = group.topic === '__other__' ? 'Other' : group.topic;
    sidebarHtml += `
      <label title="${esc(label)}">
        <input type="checkbox" checked data-topic="${esc(group.topic)}"
               onchange="filterTopic(this)">
        ${esc(label)} <span style="color:var(--text-dim)">(${group.match_count})</span>
      </label>`;
  }
  sidebarHtml += `
      <div class="sidebar-actions">
        <button onclick="setAllTopics(true)">All</button>
        <button onclick="setAllTopics(false)">None</button>
      </div>
    </div>`;

  // Build groups HTML
  let groupsHtml = `
    <div class="summary">
      <strong>${data.total_fetched}</strong> papers fetched &nbsp;·&nbsp;
      <strong>${data.total_matched}</strong> matched across
      <strong>${topicCount}</strong> topic${topicCount !== 1 ? 's' : ''}
    </div>`;

  for (const group of data.results) {
    const gid = 'g-' + Math.random().toString(36).slice(2);
    const groupLabel = group.topic === '__other__' ? 'Other (unmatched)' : group.topic;
    groupsHtml += `
      <div class="result-group" data-topic="${esc(group.topic)}">
        <div class="result-group-header open" onclick="toggleGroup('${gid}', this)">
          ${esc(groupLabel)}
          <span class="count">${group.match_count}</span>
          <span class="chevron">▶</span>
        </div>
        <div class="result-group-body open" id="${gid}">`;

    for (const p of group.papers) {
      const authors = formatAuthors(p.authors);
      const date = (p.published || '').slice(0, 10);
      const chips = (p.matched_keywords || []).map(k => `<span class="chip">${esc(k)}</span>`).join('');
      const pid = esc(p.id);
      groupsHtml += `
          <div class="paper-row" onclick="selectPaperRow(this, '${pid}', 'paper')">
            <div style="padding:10px 16px; display:flex; gap:10px; align-items:flex-start">
              <input type="checkbox" class="paper-select" data-id="${pid}"
                     onclick="event.stopPropagation(); updateSaveBar()">
              <div style="flex:1">
                <div class="paper-title">${esc(p.title)}</div>
                <div class="paper-meta">${esc(authors)} &nbsp;·&nbsp; ${esc(date)}
                  &nbsp;·&nbsp; <span style="font-family:var(--mono);font-size:10px;color:var(--text-dim)">${pid}</span>
                </div>
                ${chips ? `<div class="paper-chips chips">${chips}</div>` : ''}
              </div>
            </div>
          </div>`;
    }

    groupsHtml += `</div></div>`;
  }

  el.innerHTML = `
    <div class="results-layout">
      ${sidebarHtml}
      <div class="results-scroll">${groupsHtml}</div>
    </div>`;
}

function selectPaperRow(row, paperId, store) {
  if (row.classList.contains('active')) {
    closeDetailPanel();
    return;
  }
  document.querySelectorAll('.paper-row.active, .library-row.active').forEach(r => r.classList.remove('active'));
  row.classList.add('active');
  const containerId = store === 'library' ? 'library-body' : 'papers-body';
  const p = store === 'library' ? libraryStore.get(paperId) : paperStore.get(paperId);
  if (p) openDetailPanel(p, containerId);
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
  document.querySelectorAll('.topic-sidebar input[type=checkbox]').forEach(cb => {
    cb.checked = checked;
    filterTopic(cb);
  });
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
  } else {
    bar.style.display = 'none';
  }
}

function clearSelection() {
  document.querySelectorAll('.paper-select:checked').forEach(cb => { cb.checked = false; });
  updateSaveBar();
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

async function loadLibrary() {
  const res = await fetch('/api/library');
  const data = await res.json();
  renderLibrary(data.papers || []);
}

function renderLibrary(papers) {
  const summaryEl = document.getElementById('library-summary');
  const listEl = document.getElementById('library-list');

  summaryEl.innerHTML = `<div class="summary"><strong>${papers.length}</strong> saved paper${papers.length !== 1 ? 's' : ''}</div>`;

  if (!papers.length) {
    listEl.innerHTML = '<div class="empty">No papers saved yet.</div>';
    return;
  }

  libraryStore.clear();
  for (const p of papers) libraryStore.set(p.id, p);

  let html = `<div style="border:1px solid var(--border);border-radius:var(--radius)">`;
  for (const p of papers) {
    const authors = formatAuthors(p.authors);
    const date = (p.published || '').slice(0, 10);
    const saved = (p.saved_at || '').slice(0, 10);
    const pid = esc(p.id);
    html += `
      <div class="library-row" onclick="selectPaperRow(this, '${pid}', 'library')">
        <div style="padding:10px 16px; display:flex; gap:10px; align-items:flex-start">
          <div style="flex:1">
            <div class="paper-title">${esc(p.title)}</div>
            <div class="paper-meta">${esc(authors)} &nbsp;·&nbsp; ${esc(date)}
              &nbsp;·&nbsp; <span style="font-family:var(--mono);font-size:10px;color:var(--text-dim)">${pid}</span>
              ${saved ? `&nbsp;·&nbsp; <span style="color:var(--text-dim)">saved ${esc(saved)}</span>` : ''}
            </div>
          </div>
          <button class="btn btn-danger" onclick="event.stopPropagation(); removeFromLibrary('${pid}')">Remove</button>
        </div>
      </div>`;
  }
  html += `</div>`;
  listEl.innerHTML = html;
}

async function removeFromLibrary(paperId) {
  const res = await fetch('/api/library/' + encodeURIComponent(paperId), { method: 'DELETE' });
  if (res.ok) {
    closeDetailPanel();
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
