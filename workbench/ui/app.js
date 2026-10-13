'use strict';

const TOKEN = '__WB_TOKEN__';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const PLATFORM_COLORS = ['#4f9cf9', '#38d39f', '#f7b955', '#e0699a', '#a78bfa', '#5ec8d8', '#f2777a', '#9fd356'];
const platformColor = (p) => {
  let h = 0;
  for (const c of String(p || '?')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PLATFORM_COLORS[h % PLATFORM_COLORS.length];
};

const STATUS_LABEL = { done: '已完成', partial: '部分完成', blocked: '受阻' };
const AGENT_LABEL = { idle: '空闲', working: '干活中', blocked: '受阻', offline: '离线' };

const state = {
  board: null,
  dossiers: [],
  allLogs: [],
  expandedAll: {},
  setup: null,
  me: JSON.parse(localStorage.getItem('wb.me') || '{"name":"","platform":"user"}'),
  filter: '',
  expanded: new Set(),
  seenLogIds: new Set(),
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', 'X-Workbench-Token': TOKEN, ...(opts.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, bad) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast' + (bad ? ' bad' : '');
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 2600);
}

const ago = (ts) => {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.floor(s)} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
};
const clock = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '';

/* ---------------- rendering ---------------- */

/* ---------------- per-platform columns ---------------- */

function dossierActions(d) {
  if (!d) {
    return `<div class="empty small">还没有产出区，归档一轮后自动出现。</div>`;
  }
  return `
    <div class="dhead">
      <b>${d.entries} 条有效</b>
      ${d.overturned ? `<span class="ov">${d.overturned} 条已推翻</span>` : ''}
    </div>
    <div class="agent-meta"><span>更新 ${esc(d.updated)}</span>${d.bytes ? `<span>${(d.bytes / 1024).toFixed(1)} KB</span>` : ''}</div>
    ${d.watermarked ? `<div class="leak">已重置过，旧条目仍在工作台数据库里</div>` : ''}
    <div class="row-actions wrap">
      <button class="ghost" data-dview="${esc(d.platform)}">预览</button>
      <button class="ghost" data-dcopytext="${esc(d.platform)}" title="复制文档文字内容">复制全文</button>
      <button class="ghost primary-ish" data-dcopyfile="${esc(d.path)}" title="把 md 文件本身放进剪贴板，可直接 Ctrl+V 粘进别的 agent 附件框">复制源文件</button>
      <button class="ghost" data-dreveal="${esc(d.path)}" title="在资源管理器中定位该文件">所在文件夹</button>
      ${d.watermarked ? `<button class="ghost warn" data-dundo="${esc(d.platform)}">撤销重置</button>` : ''}
    </div>`;
}

function feedItem(l) {
  const open = state.expanded.has(l.id);
  return `
  <article class="card log" data-log="${l.id}">
    <button class="log-q" data-toggle="${l.id}">
      <b>${esc(l.question)}</b>
      <span class="chev">${open ? '收起' : '展开'}</span>
    </button>
    <div class="log-meta">
      <span>${esc(l.agent_id)}</span><span>${clock(l.created_at)}</span>
      <span>${STATUS_LABEL[l.status] || esc(l.status)}</span>
      <span class="src">${esc(l.source)}</span>
      ${l.name_modified ? '<span title="问题含文件系统非法字符，已替换为全角等价字符">文件名已适配</span>' : ''}
    </div>
    ${open ? `
      <div class="log-full">
        <h5>一、本次做了什么</h5><div class="md">${esc(l.actions || '（未记录）')}</div>
        <h5>二、回答内容</h5><div class="md">${esc(l.answer || '（空）')}</div>
        ${l.next_steps ? `<h5>三、后续待办</h5><div class="md">${esc(l.next_steps)}</div>` : ''}
        ${(l.artifacts || []).length ? `<h5>产出文件</h5><ul class="arts">${l.artifacts.map(a => `<li><code>${esc(a)}</code></li>`).join('')}</ul>` : ''}
        ${l.md_path ? `<div class="logpath"><code>${esc(l.md_path)}</code></div>` : ''}
      </div>` : ''}
  </article>`;
}

function agentChip(a) {
  return `
    <span class="chip ${a.online ? '' : 'dim'}" title="${esc(a.agent_id)} · ${AGENT_LABEL[a.effective_status] || a.effective_status} · ${a.online ? a.seen_seconds_ago + ' 秒前心跳' : '离线'}">
      <i class="dot ${a.effective_status}"></i>${esc(a.display_name || a.agent_id)}
      <button class="mini" data-ping="${esc(a.agent_id)}" title="刷新心跳">↻</button>
      <button class="mini danger-ish" data-ragent="${esc(a.agent_id)}" title="只重置这个 agent 的回答时间线（含其 md 文件）">✕</button>
    </span>`;
}

function platformColumn(plat, agents, dossierMeta, logs) {
  const online = agents.filter(a => a.online);
  const leaked = agents.filter(a => a.suspected_missed_archive);
  const shown = logs.slice(0, state.expandedAll[plat] ? logs.length : 8);
  return `
  <section class="pcolumn" data-platform="${esc(plat)}">
    <header class="phead" style="--c:${platformColor(plat)}">
      <div class="phead-top">
        <span class="badge solid">${esc(plat)}</span>
        <span class="dot ${online.length ? (online[0].effective_status) : 'offline'}"></span>
        <b class="pname">${online.length}/${agents.length} 在线</b>
        <span class="right count">${logs.length} 条归档</span>
      </div>
      <div class="prow-actions">
        <button class="mini danger-ish" data-rplat-dossier="${esc(plat)}" title="清空本平台交接文档，归档历史保留">重置产出区</button>
        <button class="mini danger-ish" data-rplat-timeline="${esc(plat)}" title="删除本平台归档记录及其 md 文件">重置时间线</button>
      </div>
      <div class="chips">${agents.map(agentChip).join('')}</div>
      ${leaked.length ? `<div class="leak">⚠ ${leaked.map(a => esc(a.agent_id)).join('、')} 有心跳但久未归档，可能漏了</div>` : ''}
    </header>

    <div class="psec">
      <h3>内容产出区</h3>
      <div class="card dossier">${dossierActions(dossierMeta)}</div>
    </div>

    <div class="psec grow">
      <h3>回答时间线<span class="count">${logs.length}</span></h3>
      <div class="stack">
        ${shown.length ? shown.map(feedItem).join('') : '<div class="empty small">还没有归档记录。</div>'}
        ${logs.length > shown.length ? `<button class="ghost morebtn" data-more="${esc(plat)}">还有 ${logs.length - shown.length} 条，展开全部</button>` : ''}
      </div>
    </div>
  </section>`;
}

function renderPlatforms() {
  const b = state.board;
  const byPlatform = new Map();
  const add = (p) => { if (!byPlatform.has(p)) byPlatform.set(p, { agents: [], logs: [] }); return byPlatform.get(p); };

  for (const a of (b.agents || [])) add(a.platform || 'unknown').agents.push(a);
  for (const l of (state.allLogs || [])) add(l.platform || 'unknown').logs.push(l);

  let plats = [...byPlatform.keys()].sort();
  if (state.filter) plats = plats.filter(p => p === state.filter);

  const dmap = Object.fromEntries((state.dossiers || []).map(d => [d.platform, d]));

  if (!plats.length) {
    $('#platforms').innerHTML = `<div class="welcome">
      <h2>还没有平台接入</h2>
      <p>点右上角 <b>接入配置</b> 拿 MCP 连接器配置；或直接把归档 .md 丢进投递目录：<br>
      <code>${esc(b.paths?.inbox || '')}</code></p>
      <div class="intake">
        <div class="intake-item"><b>MCP</b><span>任意支持 MCP 的客户端，一行配置接入</span></div>
        <div class="intake-item"><b>HTTP</b><span>localhost API，hook / 脚本 / CLI 用</span></div>
        <div class="intake-item"><b>文件</b><span>往投递目录丢 .md / .json 即可</span></div>
      </div>
    </div>`;
    return;
  }
  $('#platforms').innerHTML = plats.map(p => platformColumn(
    p,
    byPlatform.get(p).agents.sort((x, y) => (y.online - x.online) || x.agent_id.localeCompare(y.agent_id)),
    dmap[p],
    byPlatform.get(p).logs.sort((x, y) => y.created_at - x.created_at),
  )).join('');
}

function renderMetrics() {
  const b = state.board;
  if (!b) return;
  const online = b.agents.filter(a => a.online).length;
  const platforms = new Set(b.agents.map(a => a.platform)).size;
  const leaked = b.agents.filter(a => a.suspected_missed_archive).length;
  $('#metrics').innerHTML = [
    ['在线 Agent', `${online}<i>/${b.agents.length}</i>`],
    ['接入平台', platforms],
    ['疑似漏归档', leaked],
    ['归档条数', (state.allLogs || []).length],
  ].map(([k, v]) => `<div class="metric"><b>${v}</b><span>${k}</span></div>`).join('');
}

function renderPaths() {
  const p = state.board?.paths || {};
  if (p.inbox) $('#inbox-path').textContent = p.inbox;
  if (p.export) $('#export-path').textContent = p.export;
}

function renderPlatformSelects() {
  const seen = [...new Set((state.board?.agents || []).map(a => a.platform))].sort();
  const cur = $('#filter-platform').value;
  $('#filter-platform').innerHTML = `<option value="">全部平台</option>` +
    seen.map(p => `<option ${p === cur ? 'selected' : ''}>${esc(p)}</option>`).join('');
  if (!seen.includes(state.me.platform) && state.me.platform !== 'user') seen.push(state.me.platform);
  const mp = $('#me-platform');
  const keep = state.me.platform;
  mp.innerHTML = ['user', ...seen].map(p => `<option ${p === keep ? 'selected' : ''}>${esc(p)}</option>`).join('');
  state.me.platform = keep;
  mp.value = keep;
}

function render() {
  renderMetrics(); renderPlatformSelects(); renderPlatforms(); renderPaths();
}

/* ---------------- data ---------------- */

async function refresh() {
  try {
    state.board = await api('/api/board');
    // Full logs, not board.logs (capped at 40 globally and would starve columns).
    state.allLogs = (await api('/api/logs?limit=400').catch(() => ({ logs: [] }))).logs;
    state.dossiers = (await api('/api/dossiers').catch(() => ({ dossiers: [] }))).dossiers;
    render();
    setConn(true);
  } catch (e) {
    setConn(false, e.message);
  }
}

function setConn(ok, msg) {
  const el = $('#conn');
  el.className = 'conn' + (ok ? '' : ' bad');
  el.querySelector('em').textContent = ok ? '已连接' : (msg || '断开');
}

/* ---------------- identity ---------------- */

function saveMe() {
  state.me.name = $('#me-name').value.trim();
  state.me.platform = $('#me-platform').value || 'user';
  localStorage.setItem('wb.me', JSON.stringify(state.me));
}

/* ---------------- MCP snippet ---------------- */

/* ---------------- onboarding guide (generated by the server) ---------------- */

function codeBlock(text) {
  return `<pre class="snippet">${esc(text)}</pre>`;
}

function cautionList(items) {
  if (!items || !items.length) return '';
  return `<ul class="cautions">${items.map(c => `<li>${esc(c)}</li>`).join('')}</ul>`;
}

async function showSetup() {
  let g;
  try { g = await api('/api/setup'); } catch (e) { toast(`读取接入指南失败：${e.message}`, true); return; }
  state.setup = g;
  const r = g.runtime;
  const skillBlocks = (g.skills || []).map((s, i) => `
    <section class="step">
      <h4><span class="badge" style="--c:${platformColor(s.platform)}">${esc(s.platform)}</span>
          <code>${esc(s.skill_name)}</code>
          <span class="hint">· ${s.bytes} B ${s.dir_exists ? '' : '· 父目录不存在'}</span></h4>
      <p class="hint">装到 <code>${esc(s.target_dir)}</code></p>
      <div class="row-actions">
        <button class="primary" data-copy-skill-inst="${i}">复制「给 agent 的安装指令」</button>
        <button class="ghost" data-copy-skill-raw="${i}">只复制 SKILL.md 原文</button>
        <button class="ghost" data-view-skill="${i}">看内容</button>
      </div>
    </section>`).join('');

  const blocks = g.steps.map((s, i) => `
    <section class="step">
      <h4>${esc(s.title)}</h4>
      <p>${esc(s.body)}</p>
      ${s.code ? codeBlock(s.code) : ''}
      ${cautionList(s.cautions)}
      ${s.code ? `<div class="row-actions"><button class="ghost" data-copy-block="${i}">复制这段</button></div>` : ''}
    </section>`).join('');
  const falls = g.fallbacks.map(f => `
    <section class="step">
      <h4>${esc(f.name)}</h4><p>${esc(f.how)}</p>${codeBlock(f.code)}
    </section>`).join('');
  closeModal('接入指南', `
    <div class="rtinfo">
      <span>运行方式 <b>${r.packaged ? '打包 exe' : '源码'}</b></span>
      <span>地址 <b>${esc(r.url)}</b></span>
      <span>已接入平台 <b>${g.platforms_seen.length ? esc(g.platforms_seen.join('、')) : '暂无'}</b></span>
    </div>
    <nav class="tabs" id="setup-tabs">
      <button data-tab="steps" class="on">接入四步</button>
      <button data-tab="skills">安装 Skill</button>
      <button data-tab="alt">其他方式</button>
      <button data-tab="paths">路径与环境变量</button>
      <button data-tab="warn">已知坑</button>
    </nav>
    <div class="tabpane" data-pane="steps">${blocks}</div>
    <div class="tabpane" data-pane="skills" hidden>
      <p class="hint">下面每段都可以直接复制后整条丢给对应平台的 agent，<b>它会把 skill 装进自己的技能目录</b>，你不用手动放文件。
      安装指令里已经带上目标路径、备份要求和「装完先别执行」的约束。</p>
      ${skillBlocks || '<p class="hint">没有找到 adapters/&#42;/SKILL.md</p>'}
    </div>
    <div class="tabpane" data-pane="alt" hidden>${falls}</div>
    <div class="tabpane" data-pane="paths" hidden>${codeBlock(
`MCP 命令   ${r.mcp_command} ${r.mcp_args.join(' ') || ''}
GUI       ${r.gui_exe || '（源码运行，用 py -m workbench.app）'}
数据库     ${r.db_path}
令牌       ${r.token_file}
投递目录   ${r.inbox}
产出区     ${r.handoff}
导出目录   ${r.logs}
桌面镜像   ${r.desktop_export ? '开' : '关'}   桌面摄取   ${r.desktop_adopt ? '开' : '关'}

关掉桌面写入      set AGENT_WORKBENCH_DESKTOP=0
关掉桌面摄取      set AGENT_WORKBENCH_ADOPT_DESKTOP=0
`)}</div>
    <div class="tabpane" data-pane="warn" hidden>${cautionList(g.warnings)}</div>
    <div class="row-actions"><button class="primary" id="copy-guide">复制纯文本全文</button></div>`);
}

/* ---------------- resets ---------------- */

function fileList(paths, cap = 12) {
  if (!paths.length) return '<p class="hint">（无）</p>';
  const shown = paths.slice(0, cap).map(p => `<li><code>${esc(p)}</code></li>`).join('');
  const more = paths.length > cap ? `<li class="more">…还有 ${paths.length - cap} 个</li>` : '';
  return `<ul class="filelist">${shown}${more}</ul>`;
}

async function confirmTimelineReset(scope) {
  const q = new URLSearchParams();
  if (scope.platform) q.set('platform', scope.platform);
  if (scope.agent) q.set('agent_id', scope.agent);
  let p;
  try { p = (await api(`/api/reset/preview?${q}`)).timeline; } catch (e) { toast(e.message, true); return; }
  if (!p.entries && !p.export_count && !p.desktop_count) { toast('这个范围内没有可重置的内容'); return; }
  const who = scope.agent ? `agent「${scope.agent}」` : (scope.platform ? `平台「${scope.platform}」` : '全部');
  await confirmReset(`重置回答时间线 · ${who}`, `
    <p class="hint">范围：<b>${esc(who)}</b> —— 将删除 <b>${p.entries}</b> 条归档记录及对应 md 文件。<b>不可撤销。</b></p>
    <h5>导出目录（${p.export_count} 个）</h5>${fileList(p.export_files)}
    <h5>桌面 · 本程序创建的镜像（${p.desktop_ours.length} 个）</h5>${fileList(p.desktop_files.filter(f=>p.desktop_ours.some(n=>f.endsWith(n))))}
    <h5>桌面 · 不是本程序创建的（${p.desktop_foreign.length} 个）${p.desktop_foreign.length?'<span class="warnTag">⚠ 这些是你或别的工具写的</span>':''}</h5>${fileList(p.desktop_files.filter(f=>p.desktop_foreign.some(n=>f.endsWith(n))))}
    ${p.desktop_foreign.length ? `<label class="chk"><input type="checkbox" id="kill-foreign" checked> 连这些非本程序创建的桌面文件一起删</label>` : ''}
    <p class="hint">其他平台的归档和产出区不受影响。受影响平台的产出区会随之重建。</p>`,
    async () => {
      const killF = document.querySelector('#kill-foreign')?.checked ?? true;
      const r = (await api('/api/reset/timeline', { method: 'POST', body: JSON.stringify({
        confirm: true, platform: scope.platform || null, agent_id: scope.agent || null,
        delete_foreign_desktop: killF }) })).result;
      const failed = [...(r.export_files_failed || []), ...(r.desktop_files_failed || [])];
      return `已删除 ${r.entries_removed} 条归档、${r.export_files_deleted.length + r.desktop_files_deleted.length} 个文件`
        + (failed.length ? `；${failed.length} 个删除失败` : '');
    });
}

async function confirmDossierReset(platforms) {
  let d;
  try { d = (await api('/api/reset/preview?platform=' + encodeURIComponent(platforms[0]))).dossier; }
  catch (e) { toast(e.message, true); return; }
  if (!d.platforms.length) { toast('该平台还没有产出区'); return; }
  const rows = d.platforms.map(p =>
    `<li><b>${esc(p)}</b> — 当前有效 ${d.current_entries[p]} 条${d.watermarks[p] ? '（已重置过一次）' : ''}</li>`).join('');
  await confirmReset('重置内容产出区 · ' + platforms.join('、'), `
    <p class="hint">只清空下面这些平台的交接文档。<b>归档记录不会删除</b>，只是不再出现在交接文档里。</p>
    <ul class="filelist">${rows}</ul>
    <p class="hint">产出区是「每平台一份文件」，所以粒度只能到平台，不能到单个 agent。之后每轮输入输出照常追加。想找回旧内容点「撤销重置」。</p>`,
    async () => {
      const r = (await api('/api/reset/dossier', { method: 'POST',
        body: JSON.stringify({ confirm: true, platforms }) })).result;
      const n = Object.values(r.platforms).reduce((a, x) => a + x.hidden_entries, 0);
      return `已清空 ${Object.keys(r.platforms).length} 个平台的产出区（隐去 ${n} 条旧条目）`;
    });
}

async function confirmReset(title, bodyHtml, run) {
  closeModal(title, `${bodyHtml}
    <div class="row-actions">
      <button class="danger" id="do-reset">确认执行，不可撤销</button>
      <button class="ghost" data-close>取消</button>
    </div>`);
  $('#do-reset').onclick = async () => {
    $('#modal').hidden = true;
    try {
      const out = await run();
      toast(out);
      await refresh();
    } catch (e) { toast(e.message, true); }
  };
}

/* ---------------- events ---------------- */

function connectSSE() {
  let es;
  try { es = new EventSource('/api/events'); } catch { return startPolling(); }
  let pending = null;
  const bump = () => { clearTimeout(pending); pending = setTimeout(refresh, 250); };
  es.onopen = () => { setConn(true); };
  es.onerror = () => { setConn(false, 'SSE 断开，轮询兜底'); startPolling(); };
  ['turn_log', 'task_created', 'task_claimed', 'task_updated', 'agent_registered', 'message']
    .forEach(k => es.addEventListener(k, bump));
  startPolling(true);
}

let pollTimer = null;
function startPolling(light) {
  if (pollTimer) return;
  pollTimer = setInterval(() => { refresh().catch(() => {}); }, light ? 8000 : 4000);
}

function openDrawer(title, html) {
  $('#drawer-title').textContent = title;
  $('#drawer-body').innerHTML = html;
  $('#drawer').hidden = false;
}
function closeModal(title, html) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = html;
  $('#modal').hidden = false;
}

async function copyText(t) {
  try { await navigator.clipboard.writeText(t); return true; }
  catch {
    const ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch {}
    ta.remove(); return ok;
  }
}

/* ---------------- wiring ---------------- */

document.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-close]');
  if (t) { t.closest('.drawer,.modal').hidden = true; return; }

  const btn = e.target.closest('button');
  if (!btn) return;

  try {
    if (btn.dataset.dview) {
      const resp = await fetch(`/api/dossiers/${encodeURIComponent(btn.dataset.dview)}`);
      const text = await resp.text();
      if (!resp.ok) throw new Error(text.slice(0, 120));
      openDrawer(`${btn.dataset.dview} · 交接档案`,
        `<p class="hint">这就是要拖给别的 agent 读的文件。开头 ⚠️ 那段是给接收方的只读约定，正文全部在围栏之内。</p>
         <pre class="snippet">${esc(text)}</pre>
         <div class="row-actions"><button class="primary" id="dcp">复制全文</button></div>`);
      $('#dcp').onclick = async () => toast((await copyText(text)) ? '档案已复制' : '复制失败');
    } else if (btn.dataset.dcopytext) {
      const r = await fetch(`/api/dossiers/${encodeURIComponent(btn.dataset.dcopytext)}`);
      const text = await r.text();
      toast((await copyText(text)) ? `${btn.dataset.dcopytext} 全文已复制` : '复制失败', true);
    } else if (btn.dataset.dcopyfile) {
      const out = await api('/api/shell?action=copy-file', {
        method: 'POST', body: JSON.stringify({ path: btn.dataset.dcopyfile }) });
      toast('源文件已进剪贴板，可直接 Ctrl+V 粘贴');
      void out;
    } else if (btn.dataset.dreveal) {
      await api('/api/shell?action=reveal', { method: 'POST', body: JSON.stringify({ path: btn.dataset.dreveal }) });
    } else if (btn.dataset.dundo) {
      const r = (await api('/api/reset/dossier', { method: 'POST',
        body: JSON.stringify({ confirm: true, undo: true, platform: btn.dataset.dundo }) })).result;
      toast(`已恢复 ${r.restored_entries} 条到 ${r.platform} 产出区`);
      refresh();
    } else if (btn.dataset.rplatDossier) {
      await confirmDossierReset([btn.dataset.rplatDossier]);
    } else if (btn.dataset.rplatTimeline) {
      await confirmTimelineReset({ platform: btn.dataset.rplatTimeline });
    } else if (btn.dataset.ragent) {
      const plat = btn.closest('.pcolumn')?.dataset.platform || null;
      await confirmTimelineReset({ platform: plat, agent: btn.dataset.ragent });
    } else if (btn.dataset.more) {
      state.expandedAll[btn.dataset.more] = true;
      renderPlatforms();
    } else if (btn.dataset.toggle) {
      const lid = btn.dataset.toggle;
      state.expanded.has(lid) ? state.expanded.delete(lid) : state.expanded.add(lid);
      renderPlatforms();
      const l = (state.allLogs || []).find(x => x.id === lid);
      if (l && state.expanded.has(lid) && !l.answer) {
        const full = await api(`/api/logs/${lid}`);
        Object.assign(l, full.log);
        renderPlatforms();
      }
    } else if (btn.dataset.ping) {
      await api(`/api/agents/${encodeURIComponent(btn.dataset.ping)}/heartbeat`, { method: 'POST', body: JSON.stringify({}) });
      toast('心跳已刷新');
      refresh();
    }
  } catch (err) { toast(err.message, true); }
});

$('#filter-platform').addEventListener('change', (e) => { state.filter = e.target.value; render(); });
$('#me-name').addEventListener('change', saveMe);
$('#me-platform').addEventListener('change', saveMe);
$('#copy-inbox').addEventListener('click', async () => {
  toast((await copyText($('#inbox-path').textContent)) ? '投递目录已复制' : '复制失败', true);
});
$('#open-inbox').addEventListener('click', async () => {
  const p = $('#inbox-path').textContent;
  if (window.pywebview?.api?.open_path) { try { await window.pywebview.api.open_path(p); return; } catch {} }
  toast(`路径：${p}`);
});
$('#show-snippet').addEventListener('click', showSetup);

document.addEventListener('click', async (e) => {
  const tab = e.target.closest('#setup-tabs button');
  if (tab) {
    $$('#setup-tabs button').forEach(b => b.classList.remove('on'));
    tab.classList.add('on');
    const name = tab.dataset.tab;
    $$('.tabpane').forEach(p => { p.hidden = p.dataset.pane !== name; });
    return;
  }
  const cs = e.target.closest('[data-copy-skill-inst]');
  if (cs && state.setup) {
    const s = state.setup.skills[+cs.dataset.copySkillInst];
    toast((await copyText(s.instruction)) ? `${s.platform} 安装指令已复制，去粘给那个 agent` : '复制失败', true);
    return;
  }
  const cr = e.target.closest('[data-copy-skill-raw]');
  if (cr && state.setup) {
    const s = state.setup.skills[+cr.dataset.copySkillRaw];
    toast((await copyText(s.text)) ? `${s.skill_name} 原文已复制` : '复制失败', true);
    return;
  }
  const cv = e.target.closest('[data-view-skill]');
  if (cv && state.setup) {
    const s = state.setup.skills[+cv.dataset.viewSkill];
    openDrawer(`${s.platform} · ${s.skill_name}`,
      `<p class="hint">目标：<code>${esc(s.target_dir)}</code></p><pre class="snippet">${esc(s.text)}</pre>`);
    return;
  }
  const cb = e.target.closest('[data-copy-block]');
  if (cb && state.setup) {
    const step = state.setup.steps[+cb.dataset.copyBlock];
    if (step?.code) toast((await copyText(step.code)) ? '已复制该段' : '复制失败', true);
    return;
  }
  if (e.target.id === 'copy-guide' && state.setup) {
    const txt = await fetch('/api/setup?format=text').then(r => r.text());
    toast((await copyText(txt)) ? '接入指南全文已复制' : '复制失败', true);
  }
});

$('#me-name').value = state.me.name;
refresh().then(() => {
  const mp = $('#me-platform');
  mp.value = state.me.platform;
  connectSSE();
});
