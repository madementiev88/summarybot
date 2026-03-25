/**
 * RSO Mini App — main application logic.
 * Handles navigation, command execution, KOS recording, Preza recording.
 */

// ── Telegram Web App SDK ──────────────────────────────

const tg = window.Telegram?.WebApp;
let initData = '';

if (tg) {
  tg.ready();
  tg.expand();
  initData = tg.initData || '';
}

// ── Auth check on load ───────────────────────────────

let userRole = 'denied';
let rgoChatId = null;

async function checkAccess() {
  try {
    const resp = await apiCall('/api/rgo/role');

    if (resp.role === 'admin') {
      userRole = 'admin';
      document.getElementById('access-denied').style.display = 'none';
      document.getElementById('app-content').style.display = 'block';
      document.getElementById('rgo-dashboard').style.display = 'none';
      // Show advisor FAB for admin
      document.getElementById('advisor-fab').style.display = 'flex';
      return true;
    } else if (resp.role === 'rgo') {
      userRole = 'rgo';
      rgoChatId = resp.chat_id;
      window._rgoRank = resp.rank;
      window._rgoTotal = resp.total_rgo;
      document.getElementById('access-denied').style.display = 'none';
      document.getElementById('app-content').style.display = 'none';
      document.getElementById('rgo-dashboard').style.display = 'flex';

      // Set greeting
      const nameEl = document.getElementById('rgo-dash-name');
      if (nameEl) nameEl.textContent = `Привет, ${resp.first_name || 'друг'} 👋`;
      const dateEl = document.getElementById('rgo-dash-date');
      if (dateEl) {
        const now = new Date();
        const days = ['воскресенье','понедельник','вторник','среда','четверг','пятница','суббота'];
        const months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
        dateEl.textContent = `${days[now.getDay()]}, ${now.getDate()} ${months[now.getMonth()]}`;
      }

      // Show advisor FAB for RGO
      document.getElementById('advisor-fab').style.display = 'flex';

      // Auto-load home tab
      loadRgoHome();
      return true;
    } else {
      document.getElementById('access-denied').style.display = 'block';
      document.getElementById('app-content').style.display = 'none';
      document.getElementById('rgo-dashboard').style.display = 'none';
      return false;
    }
  } catch (e) {
    document.getElementById('app-content').style.display = 'block';
    return true;
  }
}

// ── RGO Dashboard — friendly AI helper ───────────────

function switchRgoTab(tab) {
  ['home', 'tasks', 'team', 'analytics'].forEach(t => {
    const el = document.getElementById('rgo-tab-' + t);
    if (el) el.style.display = t === tab ? 'block' : 'none';
    const nav = document.getElementById('rd-nav-' + t);
    if (nav) nav.classList.toggle('active', t === tab);
  });
  if (tab === 'home') loadRgoHome();
  else if (tab === 'tasks') loadRgoTasks();
  else if (tab === 'team') loadRgoTeam();
  else if (tab === 'analytics') loadRgoAnalytics();
}

async function loadRgoHome() {
  const container = document.getElementById('rgo-tips-content');
  if (!container) return;
  container.innerHTML = '<div class="rd-loading">Загрузка...</div>';

  try {
    const [tips, team] = await Promise.all([
      apiCall('/api/rgo/tips'),
      apiCall('/api/rgo/team'),
    ]);
    let html = '';

    // Focus block (green)
    const focusItems = [];
    if (tips.focus) tips.focus.forEach(f => focusItems.push(f.text));
    if (tips.glossary) tips.glossary.forEach(g => focusItems.push('От НУ: ' + g.text.replace(/🔴\s?/, '')));

    if (focusItems.length > 0) {
      html += '<div class="rd-focus"><div class="rd-focus-label">фокус на сегодня</div>';
      focusItems.slice(0, 3).forEach((text, i) => {
        html += `<div class="rd-focus-item"><div class="rd-focus-num">${i+1}</div><div class="rd-focus-text">${text}</div></div>`;
      });
      html += '</div>';
    }

    // AI tips (purple) — from recommendation
    if (tips.recommendation) {
      html += '<div class="rd-tips"><div class="rd-tips-label">замечаю кое-что</div><div class="rd-tips-sub">для тебя</div>';
      // Split recommendation by newlines into separate tips
      const lines = tips.recommendation.replace(/<[^>]+>/g, '').split('\n').filter(l => l.trim().length > 10);
      lines.slice(0, 3).forEach(line => {
        html += `<div class="rd-tips-item">${line.trim()}</div>`;
      });
      html += '</div>';
    }

    // Team dots
    if (team.top_week && team.top_week.length > 0) {
      html += '<div class="rd-team-dots-block"><div class="rd-section-label">команда</div><div class="rd-team-dots">';
      const todayActive = new Set();
      // Assume top_week names are active this week
      team.top_week.forEach(p => {
        html += `<div class="rd-team-person"><span class="rd-dot rd-dot-green"></span>${p.name.split(' ')[0]}</div>`;
        todayActive.add(p.name);
      });
      if (team.silent_members) {
        team.silent_members.forEach(name => {
          if (!todayActive.has(name)) {
            html += `<div class="rd-team-person"><span class="rd-dot rd-dot-red"></span>${name.split(' ')[0]}</div>`;
          }
        });
      }
      html += '</div></div>';
    }

    // Analytics mini
    html += `<div class="rd-analytics-mini"><div class="rd-section-label">аналитика</div>
      <div class="rd-an-row">
        <div class="rd-an-card"><div class="rd-an-num">${team.today?.messages || 0}</div><div class="rd-an-sub">сообщений сегодня</div></div>
        <div class="rd-an-card"><div class="rd-an-num">${team.today?.participants || 0}</div><div class="rd-an-sub">участников</div></div>
      </div></div>`;

    if (!html) html = '<div class="rd-empty">Пока нет данных</div>';
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="rd-error">Ошибка загрузки</div>';
  }
}

async function loadRgoTasks() {
  const container = document.getElementById('rgo-tasks-content');
  if (!container) return;
  container.innerHTML = '<div class="rd-loading">Загрузка...</div>';

  try {
    const data = await apiCall('/api/rgo/tasks');
    let html = '';

    // Counters
    html += `<div class="rd-counters">
      <div class="rd-counter"><div class="rd-counter-num rd-counter-num-fire">${data.stats.overdue}</div><div class="rd-counter-label">горят 🔥</div></div>
      <div class="rd-counter"><div class="rd-counter-num rd-counter-num-open">${data.stats.open}</div><div class="rd-counter-label">открыто</div></div>
      <div class="rd-counter"><div class="rd-counter-num rd-counter-num-done">${data.stats.closed_today}</div><div class="rd-counter-label">готово сегодня</div></div>
    </div>`;

    // НУ orders
    if (data.glossary && data.glossary.length > 0) {
      html += '<div class="rd-nu-block"><div class="rd-nu-label">от НУ</div>';
      data.glossary.forEach(g => {
        html += `<div class="rd-nu-item">${g.text}</div>`;
      });
      html += '</div>';
    }

    // Tasks
    if (data.tasks && data.tasks.length > 0) {
      data.tasks.forEach(t => {
        const isFire = t.status === 'overdue';
        const cls = isFire ? 'rd-task-card rd-task-fire' : 'rd-task-card';
        const days = t.due_date ? Math.max(0, Math.floor((Date.now() - new Date(t.due_date)) / 86400000)) : 0;
        const meta = isFire ? `<div class="rd-task-meta-fire">🔥 горит ${days} дн.</div>` :
          `<div class="rd-task-meta">${t.assigner || ''}</div>`;
        html += `<div class="${cls}">
          <div class="rd-task-title">${t.text}</div>
          ${meta}
          <button class="rd-task-btn" onclick="closeTask(${t.id})">готово ✓</button>
        </div>`;
      });
    } else {
      html += '<div class="rd-empty">Нет открытых задач 🎉</div>';
    }

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="rd-error">Ошибка загрузки</div>';
  }
}

async function loadRgoTeam() {
  const container = document.getElementById('rgo-team-content');
  if (!container) return;
  container.innerHTML = '<div class="rd-loading">Загрузка...</div>';

  try {
    const data = await apiCall('/api/rgo/team');
    let html = '';

    // Member grid
    const todayUserNames = new Set();
    html += '<div class="rd-member-grid">';
    if (data.top_week) {
      data.top_week.forEach(p => {
        const isSilent = data.silent_members && data.silent_members.includes(p.name);
        const dotColor = isSilent ? '#ffc107' : '#4caf50';
        const sub = isSilent ? 'молчит сегодня' : 'активен';
        html += `<div class="rd-member-card"><span class="rd-member-dot" style="background:${dotColor}"></span><div><div class="rd-member-name">${p.name.split(' ')[0]}</div><div class="rd-member-sub">${sub}</div></div></div>`;
        todayUserNames.add(p.name);
      });
    }
    if (data.silent_members) {
      data.silent_members.forEach(name => {
        if (!todayUserNames.has(name)) {
          html += `<div class="rd-member-card"><span class="rd-member-dot" style="background:#e53935"></span><div><div class="rd-member-name">${name.split(' ')[0]}</div><div class="rd-member-sub">молчит</div></div></div>`;
        }
      });
    }
    html += '</div>';

    // Attention block
    if (data.silent_members && data.silent_members.length > 0) {
      html += '<div class="rd-attention"><div class="rd-attention-label">стоит заглянуть</div>';
      data.silent_members.forEach(name => {
        html += `<div class="rd-attention-item">${name} давно не писал — может стоит проверить как дела</div>`;
      });
      html += '</div>';
    }

    // Top week
    if (data.top_week && data.top_week.length > 0) {
      html += '<div class="rd-top-block"><div class="rd-section-label">топ за неделю</div>';
      data.top_week.forEach((p, i) => {
        const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : '&nbsp;&nbsp;&nbsp;&nbsp;';
        html += `<div class="rd-top-item">${medal} ${p.name} — <b>${p.messages}</b> сообщ.</div>`;
      });
      html += '</div>';
    }

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="rd-error">Ошибка загрузки</div>';
  }
}

async function loadRgoAnalytics() {
  const container = document.getElementById('rgo-analytics-content');
  if (!container) return;
  container.innerHTML = '<div class="rd-loading">Загрузка...</div>';

  try {
    const data = await apiCall('/api/rgo/team');
    const tasks = await apiCall('/api/rgo/tasks');
    let html = '';

    // Today metrics
    html += `<div class="rd-an-section"><div class="rd-section-label">сегодня</div>
      <div class="rd-an-metrics">
        <div class="rd-an-metric"><div class="rd-an-big">${data.today?.messages || 0}</div><div class="rd-an-small">сообщений</div></div>
        <div class="rd-an-metric"><div class="rd-an-big">${data.today?.participants || 0}</div><div class="rd-an-small">участников</div></div>
        <div class="rd-an-metric"><div class="rd-an-big">${tasks.stats?.open || 0}</div><div class="rd-an-small">задач открыто</div>
          ${tasks.stats?.overdue ? `<div class="rd-an-trend-red">${tasks.stats.overdue} горят</div>` : ''}
        </div>
      </div></div>`;

    // Trend (simplified — compare week data)
    html += `<div class="rd-an-section"><div class="rd-section-label">тренд за неделю</div>
      <div class="rd-an-metrics">
        <div class="rd-an-metric"><div class="rd-an-big">${data.top_week ? data.top_week.reduce((s,p) => s + p.messages, 0) : 0}</div><div class="rd-an-small">сообщений за неделю</div></div>
        <div class="rd-an-metric"><div class="rd-an-big">${data.top_week?.length || 0}</div><div class="rd-an-small">активных участников</div></div>
      </div></div>`;

    // Rank
    if (window._rgoRank && window._rgoTotal) {
      html += `<div class="rd-an-section"><div class="rd-section-label">среди рго</div>
        <div class="rd-an-rank"><div class="rd-an-rank-text">🏅 Твоя команда: ${window._rgoRank} из ${window._rgoTotal} по активности</div></div>
      </div>`;
    }

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="rd-error">Ошибка загрузки</div>';
  }
}

async function closeTask(taskId) {
  try {
    await apiCall(`/api/rgo/tasks/${taskId}/close`, { method: 'POST' });
    showToast('Готово ✓');
    loadRgoTasks();
  } catch (e) {
    showToast('Ошибка');
  }
}

checkAccess();

// ── State ─────────────────────────────────────────────

let currentSection = 'default';
const recorder = new AudioRecorder();
let kosRecording = false;
let prezaRecording = false;
let glossaryRecording = false;

const sections = ['default', 'rgo', 'kos', 'preza', 'glossary'];
const cats = ['c-sys', 'c-rep', 'c-gr', 'c-por', 'c-an', 'c-us', 'c-st'];

// Commands that need text input
const inputCommands = new Set(['search', 'ask', 'add_chat', 'remove_chat', 'add_keyword', 'set_role', 'report']);

// ── API helpers ───────────────────────────────────────

async function apiCall(url, options = {}) {
  const headers = {
    'Authorization': `tg-init-data ${initData}`,
    ...options.headers,
  };

  const resp = await fetch(url, { ...options, headers });
  return resp.json();
}

async function apiPost(url, body) {
  return apiCall(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function apiUpload(url, blob, filename) {
  const formData = new FormData();
  formData.append('audio', blob, filename || 'audio.webm');

  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Authorization': `tg-init-data ${initData}` },
    body: formData,
  });
  return resp.json();
}

// ── Navigation ────────────────────────────────────────

function openSection(name) {
  currentSection = name;
  sections.forEach(s => {
    const el = document.getElementById('body-' + s);
    if (el) el.style.display = s === name ? 'block' : 'none';
  });

  // Highlight tile
  document.querySelectorAll('.tile').forEach(t => t.classList.remove('active-tile'));
  const tile = document.getElementById('tile-' + name);
  if (tile) tile.classList.add('active-tile');

  // Header
  document.getElementById('hdr-back').style.display = 'inline';
  const titles = { rgo: 'РГО — Мониторинг', kos: 'КОС — Совещания', preza: 'Преза — Презентации', glossary: 'Глоссарий — Поручения' };
  document.getElementById('hdr-title').textContent = titles[name] || 'РСО';

  // Hide input dialog when switching
  hideInputDialog();

  // Reset result area
  hideResult();
}

function goHome() {
  currentSection = 'default';
  sections.forEach(s => {
    const el = document.getElementById('body-' + s);
    if (el) el.style.display = s === 'default' ? 'block' : 'none';
  });
  document.querySelectorAll('.tile').forEach(t => t.classList.remove('active-tile'));
  document.getElementById('hdr-back').style.display = 'none';
  document.getElementById('hdr-title').textContent = 'РСО';
  hideInputDialog();
  hideResult();
}

// ── RGO categories ────────────────────────────────────

function switchCat(btn, id) {
  document.querySelectorAll('.cat-tile').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  cats.forEach(c => {
    const el = document.getElementById(c);
    if (el) el.style.display = c === id ? 'block' : 'none';
  });
  hideInputDialog();
  hideResult();
}

// ── Command execution ─────────────────────────────────

let pendingCommand = null;

function runCmd(name) {
  if (inputCommands.has(name)) {
    // Show input dialog
    pendingCommand = name;
    const placeholders = {
      search: 'Введите текст для поиска...',
      ask: 'Задайте вопрос AI...',
      add_chat: 'Введите ID чата...',
      remove_chat: 'Введите ID чата...',
      add_keyword: 'Введите ключевое слово...',
      set_role: 'user_id role (rgo/ro/nu/other)',
      report: 'Дата (YYYY-MM-DD)...',
    };
    showInputDialog(placeholders[name] || 'Введите...');
    return;
  }

  execCommand(name, '');
}

function submitInput() {
  const input = document.getElementById('cmd-input');
  const value = input.value.trim();
  if (!value || !pendingCommand) return;

  execCommand(pendingCommand, value);
  input.value = '';
  hideInputDialog();
}

async function execCommand(name, args) {
  showProcessing('Выполняю /' + name + '...', '');

  try {
    const result = await apiPost('/api/command/' + name, { args });

    hideProcessing();

    if (result.error) {
      showToast(result.error);
    } else if (result.html) {
      showResult(result.html);
      showToast('Команда /' + name + ' выполнена');
    } else if (result.status === 'processing') {
      showToast(result.message || 'Результат придёт в бот');
    }
  } catch (err) {
    hideProcessing();
    showToast('Ошибка связи с сервером');
  }
}

// ── Input dialog ──────────────────────────────────────

function showInputDialog(placeholder) {
  const dialog = document.getElementById('input-dialog');
  const input = document.getElementById('cmd-input');
  dialog.classList.add('show');
  input.placeholder = placeholder;
  input.value = '';
  input.focus();
}

function hideInputDialog() {
  document.getElementById('input-dialog').classList.remove('show');
  pendingCommand = null;
}

// ── Result display ────────────────────────────────────

function showResult(html) {
  const el = document.getElementById('result-area');
  const content = document.getElementById('result-html');
  content.innerHTML = html;
  el.style.display = 'block';
}

function hideResult() {
  document.getElementById('result-area').style.display = 'none';
}

// ── KOS recording ─────────────────────────────────────

async function toggleKosRec() {
  if (!kosRecording) {
    // Start recording
    try {
      await recorder.start((secs) => {
        document.getElementById('kos-timer').textContent = formatTime(secs);
      });
    } catch (err) {
      showToast(err.message || 'Не удалось начать запись');
      return;
    }

    kosRecording = true;
    const tile = document.getElementById('tile-kos');
    tile.classList.add('recording');
    document.getElementById('kos-ico-wrap').classList.add('rec-bg');
    document.getElementById('kos-ico-wrap').textContent = '\u23F2';
    document.getElementById('kos-tile-sub').className = 'tile-sub-rec';
    document.getElementById('kos-tile-sub').textContent = 'Идёт запись';

    document.getElementById('kos-ring').classList.add('rec');
    document.getElementById('kos-inner').classList.add('rec');
    document.getElementById('kos-inner').textContent = '\u23F2';
    document.getElementById('kos-timer').classList.add('show');
    document.getElementById('kos-sc-dot').style.background = 'var(--red)';
    document.getElementById('kos-sc-title').textContent = 'Идёт запись';
    document.getElementById('kos-status').innerHTML = '<span class="rec-dot"></span>Запись идёт...';
    document.getElementById('kos-hint').textContent = 'Нажмите ещё раз для остановки';

  } else {
    // Stop recording
    const blob = await recorder.stop();
    kosRecording = false;
    resetKosUI();

    if (!blob || blob.size === 0) {
      showToast('Запись пуста');
      return;
    }

    // Upload and process
    showProcessing('Расшифровка аудио...', 'Whisper API');

    try {
      const result = await apiUpload('/api/kos/upload', blob, 'meeting.webm');

      if (result.task_id) {
        // Poll for result
        await pollTask(result.task_id, [
          { step: 'transcribing', text: 'Расшифровка аудио...', sub: 'Whisper API' },
          { step: 'summarizing', text: 'Анализ совещания...', sub: 'Claude AI' },
        ]);
      } else if (result.error) {
        hideProcessing();
        showToast(result.error);
      }
    } catch (err) {
      hideProcessing();
      showToast('Ошибка отправки аудио');
    }
  }
}

function resetKosUI() {
  const tile = document.getElementById('tile-kos');
  tile.classList.remove('recording');
  document.getElementById('kos-ico-wrap').classList.remove('rec-bg');
  document.getElementById('kos-ico-wrap').textContent = '\uD83C\uDFA4';
  document.getElementById('kos-tile-sub').className = 'tile-sub';
  document.getElementById('kos-tile-sub').textContent = 'Совещания';

  document.getElementById('kos-ring').classList.remove('rec');
  document.getElementById('kos-inner').classList.remove('rec');
  document.getElementById('kos-inner').textContent = '\uD83C\uDFA4';
  document.getElementById('kos-timer').classList.remove('show');
  document.getElementById('kos-sc-dot').style.background = 'var(--accent)';
  document.getElementById('kos-sc-title').textContent = 'Запись встречи';
  document.getElementById('kos-status').textContent = 'Нажмите для начала записи';
  document.getElementById('kos-hint').innerHTML = 'После остановки AI сформирует<br>краткое содержание встречи';
}

// ── Preza recording ───────────────────────────────────

async function togglePrezaRec() {
  if (!prezaRecording) {
    try {
      await recorder.start((secs) => {
        document.getElementById('preza-timer').textContent = formatTime(secs);
      });
    } catch (err) {
      showToast(err.message || 'Не удалось начать запись');
      return;
    }

    prezaRecording = true;
    const tile = document.getElementById('tile-preza');
    tile.classList.add('recording');
    document.getElementById('preza-ico-wrap').classList.add('rec-bg');
    document.getElementById('preza-ico-wrap').textContent = '\u23F2';
    document.getElementById('preza-tile-sub').className = 'tile-sub-rec';
    document.getElementById('preza-tile-sub').textContent = 'Идёт запись';

    document.getElementById('preza-orb').classList.add('rec');
    document.getElementById('preza-orb').textContent = '\u23F2';
    document.getElementById('preza-timer').classList.add('show');
    document.getElementById('preza-sc-dot').style.background = 'var(--red)';
    document.getElementById('preza-sc-title').textContent = 'Запись требований';
    document.getElementById('preza-hint').innerHTML = '<span class="rec-dot"></span>Наговаривайте требования...<br>Нажмите для остановки';

  } else {
    const blob = await recorder.stop();
    prezaRecording = false;
    resetPrezaUI();

    if (!blob || blob.size === 0) {
      showToast('Запись пуста');
      return;
    }

    showProcessing('Расшифровка аудио...', 'Whisper API');

    try {
      const result = await apiUpload('/api/preza/generate', blob, 'preza.webm');

      if (result.task_id) {
        await pollTask(result.task_id, [
          { step: 'transcribing', text: 'Расшифровка аудио...', sub: 'Whisper API' },
          { step: 'planning', text: 'Планирование слайдов...', sub: 'Claude AI (шаг 1/2)' },
          { step: 'generating', text: 'Генерация контента...', sub: 'Claude AI (шаг 2/2)' },
          { step: 'building_pptx', text: 'Сборка PPTX...', sub: '' },
        ]);
      } else if (result.error) {
        hideProcessing();
        showToast(result.error);
      }
    } catch (err) {
      hideProcessing();
      showToast('Ошибка отправки аудио');
    }
  }
}

function resetPrezaUI() {
  const tile = document.getElementById('tile-preza');
  tile.classList.remove('recording');
  document.getElementById('preza-ico-wrap').classList.remove('rec-bg');
  document.getElementById('preza-ico-wrap').textContent = '\uD83D\uDCD1';
  document.getElementById('preza-tile-sub').className = 'tile-sub';
  document.getElementById('preza-tile-sub').textContent = 'Презентации';

  document.getElementById('preza-orb').classList.remove('rec');
  document.getElementById('preza-orb').textContent = '\uD83D\uDCD1';
  document.getElementById('preza-timer').classList.remove('show');
  document.getElementById('preza-sc-dot').style.background = 'var(--accent)';
  document.getElementById('preza-sc-title').textContent = 'Создать презентацию';
  document.getElementById('preza-hint').innerHTML = 'Наговорите голосом тему,<br>содержание и стиль презентации.<br>Готовый файл придёт в бот.';
}

// ── Glossary recording ────────────────────────────────

async function toggleGlossaryRec() {
  if (!glossaryRecording) {
    try {
      await recorder.start((secs) => {
        document.getElementById('glossary-timer').textContent = formatTime(secs);
      });
    } catch (err) {
      showToast(err.message || 'Не удалось начать запись');
      return;
    }

    glossaryRecording = true;
    const tile = document.getElementById('tile-glossary');
    tile.classList.add('recording');
    document.getElementById('glossary-ico-wrap').classList.add('rec-bg');
    document.getElementById('glossary-ico-wrap').textContent = '\u23F2';
    document.getElementById('glossary-tile-sub').className = 'tile-sub-rec';
    document.getElementById('glossary-tile-sub').textContent = 'Идёт запись';

    document.getElementById('glossary-ring').classList.add('rec');
    document.getElementById('glossary-inner').classList.add('rec');
    document.getElementById('glossary-inner').textContent = '\u23F2';
    document.getElementById('glossary-timer').classList.add('show');
    document.getElementById('glossary-sc-dot').style.background = 'var(--red)';
    document.getElementById('glossary-sc-title').textContent = 'Запись поручений';
    document.getElementById('glossary-status').innerHTML = '<span class="rec-dot"></span>Запись идёт...';
    document.getElementById('glossary-hint').textContent = 'Нажмите ещё раз для остановки';

  } else {
    const blob = await recorder.stop();
    glossaryRecording = false;
    resetGlossaryUI();

    if (!blob || blob.size === 0) {
      showToast('Запись пуста');
      return;
    }

    showProcessing('Расшифровка аудио...', 'Whisper API');

    try {
      const result = await apiUpload('/api/glossary/upload', blob, 'glossary.webm');

      if (result.task_id) {
        await pollTask(result.task_id, [
          { step: 'transcribing', text: 'Расшифровка аудио...', sub: 'Whisper API' },
          { step: 'analyzing', text: 'Извлечение поручений...', sub: 'Claude AI' },
        ]);
      } else if (result.error) {
        hideProcessing();
        showToast(result.error);
      }
    } catch (err) {
      hideProcessing();
      showToast('Ошибка отправки аудио');
    }
  }
}

function resetGlossaryUI() {
  const tile = document.getElementById('tile-glossary');
  tile.classList.remove('recording');
  document.getElementById('glossary-ico-wrap').classList.remove('rec-bg');
  document.getElementById('glossary-ico-wrap').textContent = '\uD83D\uDCCB';
  document.getElementById('glossary-tile-sub').className = 'tile-sub';
  document.getElementById('glossary-tile-sub').textContent = 'Поручения';

  document.getElementById('glossary-ring').classList.remove('rec');
  document.getElementById('glossary-inner').classList.remove('rec');
  document.getElementById('glossary-inner').textContent = '\uD83D\uDCCB';
  document.getElementById('glossary-timer').classList.remove('show');
  document.getElementById('glossary-sc-dot').style.background = 'var(--accent)';
  document.getElementById('glossary-sc-title').textContent = 'Поручения для РГО';
  document.getElementById('glossary-status').textContent = 'Надиктуйте поручения для РГО';
  document.getElementById('glossary-hint').innerHTML = 'Поручения будут добавлены<br>в утренние рекомендации на завтра';
}

// ── Task polling ──────────────────────────────────────

async function pollTask(taskId, steps) {
  const maxAttempts = 120; // 4 minutes max
  let attempt = 0;

  while (attempt < maxAttempts) {
    await sleep(2000);
    attempt++;

    try {
      const data = await apiCall('/api/task/' + taskId);

      if (data.status === 'done') {
        hideProcessing();
        if (data.result?.summary) {
          showToast('Резюме отправлено в бот');
        } else if (data.result?.title) {
          showToast(data.result.title + '.pptx отправлен в бот');
        } else {
          showToast('Готово! Результат в боте');
        }
        return data.result;
      }

      if (data.status === 'error') {
        hideProcessing();
        showToast(data.result?.error || 'Ошибка обработки');
        return null;
      }

      // Update processing step
      const stepInfo = steps.find(s => s.step === data.step);
      if (stepInfo) {
        updateProcessing(stepInfo.text, stepInfo.sub);
      }

    } catch (err) {
      // Network error, keep polling
    }
  }

  hideProcessing();
  showToast('Время ожидания истекло');
  return null;
}

// ── Feedback ──────────────────────────────────────────

function openModal() {
  document.getElementById('modal').classList.add('open');
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
}

async function sendFeedback() {
  const ta = document.querySelector('.modal-ta');
  const text = ta.value.trim();
  if (!text) return;

  closeModal();
  showToast('Отправка...');

  try {
    const result = await apiPost('/api/feedback', { text });
    if (result.status === 'ok') {
      showToast('Сообщение отправлено разработчику');
      ta.value = '';
    } else {
      showToast(result.error || 'Ошибка отправки');
    }
  } catch (err) {
    showToast('Ошибка связи с сервером');
  }
}

// ── Processing overlay ────────────────────────────────

function showProcessing(step, sub) {
  document.getElementById('proc-step').textContent = step;
  document.getElementById('proc-sub').textContent = sub || '';
  document.getElementById('processing').classList.add('show');
}

function updateProcessing(step, sub) {
  document.getElementById('proc-step').textContent = step;
  document.getElementById('proc-sub').textContent = sub || '';
}

function hideProcessing() {
  document.getElementById('processing').classList.remove('show');
}

// ── Toast ─────────────────────────────────────────────

let toastTimeout = null;

function showToast(text) {
  const toast = document.getElementById('toast');
  toast.textContent = text;
  toast.classList.add('show');
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove('show'), 3000);
}

// ── Utils ─────────────────────────────────────────────

function formatTime(secs) {
  const m = String(Math.floor(secs / 60)).padStart(2, '0');
  const s = String(secs % 60).padStart(2, '0');
  return m + ':' + s;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ── Balance display ───────────────────────────────────

async function loadBalance() {
  try {
    const data = await apiCall('/api/balance');
    const el = document.getElementById('hero-balance');
    if (!el) return;

    const dailyRemaining = data.daily_remaining;

    let valueClass = '';
    if (dailyRemaining < 1) valueClass = 'danger';
    else if (dailyRemaining < 2) valueClass = 'warn';

    var balanceClass = '';
    if (data.balance < 5) balanceClass = 'danger';
    else if (data.balance < 10) balanceClass = 'warn';

    el.innerHTML =
      '<div class="balance-item">' +
        '<span class="balance-value ' + balanceClass + '">$' + data.balance.toFixed(2) + '</span>' +
        '<span class="balance-label">Баланс</span>' +
      '</div>' +
      '<div class="balance-item">' +
        '<span class="balance-value ' + valueClass + '">$' + dailyRemaining.toFixed(2) + '</span>' +
        '<span class="balance-label">Бюджет дня</span>' +
      '</div>' +
      '<div class="balance-item">' +
        '<span class="balance-value">$' + data.total_spent.toFixed(2) + '</span>' +
        '<span class="balance-label">Всего</span>' +
      '</div>';
  } catch (err) {
    // Silently ignore
  }
}

// Load balance on start and refresh every 60 seconds
loadBalance();
setInterval(loadBalance, 60000);

// ── Keyboard support for input ────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && pendingCommand) {
    e.preventDefault();
    submitInput();
  }
});

// ── Advisor Chat ──────────────────────────────────────

let advisorHistory = [];
let advisorBusy = false;

function openAdvisor() {
  document.getElementById('advisor-overlay').classList.add('open');
  document.getElementById('advisor-input').focus();
}

function closeAdvisor() {
  document.getElementById('advisor-overlay').classList.remove('open');
}

function _addAdvisorMsg(role, text) {
  const container = document.getElementById('advisor-messages');
  const div = document.createElement('div');
  div.className = `adv-msg adv-msg-${role === 'user' ? 'user' : 'bot'}`;

  const label = document.createElement('div');
  label.className = 'adv-msg-label';
  label.textContent = role === 'user' ? 'ты' : 'советник';

  const bubble = document.createElement('div');
  bubble.className = 'adv-msg-bubble';
  // Simple markdown: **bold** → <b>bold</b>
  let html = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
    .replace(/\n/g, '<br>');
  bubble.innerHTML = html;

  div.appendChild(label);
  div.appendChild(bubble);
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function _showAdvisorTyping() {
  const container = document.getElementById('advisor-messages');
  const div = document.createElement('div');
  div.className = 'adv-msg adv-msg-bot';
  div.id = 'advisor-typing';
  div.innerHTML = `
    <div class="adv-msg-label">советник</div>
    <div class="adv-msg-bubble">
      <div class="adv-typing">
        <div class="adv-typing-dot"></div>
        <div class="adv-typing-dot"></div>
        <div class="adv-typing-dot"></div>
      </div>
    </div>
  `;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function _hideAdvisorTyping() {
  const el = document.getElementById('advisor-typing');
  if (el) el.remove();
}

async function sendAdvisorQuestion() {
  if (advisorBusy) return;

  const input = document.getElementById('advisor-input');
  const question = input.value.trim();
  if (!question) return;

  // Block send button
  advisorBusy = true;
  document.getElementById('advisor-send').disabled = true;
  input.value = '';

  // Show user message
  _addAdvisorMsg('user', question);
  _showAdvisorTyping();

  try {
    const advisorUrl = userRole === 'admin' ? '/api/nu/advisor' : '/api/rgo/advisor';
    const resp = await fetch(advisorUrl, {
      method: 'POST',
      headers: {
        'Authorization': `tg-init-data ${window.Telegram?.WebApp?.initData || ''}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        question: question,
        history: advisorHistory,
      }),
    });

    _hideAdvisorTyping();

    if (!resp.ok) {
      _addAdvisorMsg('bot', '⚠️ Советник временно недоступен. Попробуй через несколько минут.');
      return;
    }

    const data = await resp.json();
    const answer = data.answer || '⚠️ Не удалось получить ответ.';

    _addAdvisorMsg('bot', answer);

    // Update history (keep last 6)
    advisorHistory.push({ role: 'user', content: question });
    advisorHistory.push({ role: 'assistant', content: answer });
    if (advisorHistory.length > 6) {
      advisorHistory = advisorHistory.slice(-6);
    }

  } catch (e) {
    _hideAdvisorTyping();
    _addAdvisorMsg('bot', '⚠️ Ошибка соединения. Проверь интернет и попробуй снова.');
  } finally {
    advisorBusy = false;
    document.getElementById('advisor-send').disabled = false;
    document.getElementById('advisor-input').focus();
  }
}
