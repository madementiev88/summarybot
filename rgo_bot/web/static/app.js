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
      return true;
    } else if (resp.role === 'rgo') {
      userRole = 'rgo';
      rgoChatId = resp.chat_id;
      document.getElementById('access-denied').style.display = 'none';
      document.getElementById('app-content').style.display = 'none';
      document.getElementById('rgo-dashboard').style.display = 'block';

      // Set RGO name and rank
      const nameEl = document.getElementById('rgo-dash-name');
      if (nameEl) nameEl.textContent = resp.first_name || 'РГО';
      const chatEl = document.getElementById('rgo-dash-chat');
      if (chatEl) chatEl.textContent = resp.chat_title || '';
      if (resp.rank && resp.total_rgo) {
        const rankEl = document.getElementById('rgo-rank-badge');
        if (rankEl) {
          rankEl.textContent = `🏅 ${resp.rank} из ${resp.total_rgo} по активности`;
          rankEl.style.display = 'inline-block';
        }
      }

      // Auto-load tips on open
      loadRgoTips();
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

// ── RGO Dashboard functions ──────────────────────────

async function loadRgoTips() {
  const container = document.getElementById('rgo-tips-content');
  if (!container) return;
  container.innerHTML = '<div class="rgo-loading">Загрузка...</div>';

  try {
    const data = await apiCall('/api/rgo/tips');
    let html = '';

    // Focus items (overdue tasks)
    if (data.focus && data.focus.length > 0) {
      html += '<div class="rgo-card"><div class="rgo-card-title">🎯 Фокус на сегодня</div>';
      data.focus.forEach(f => {
        const cls = f.status === 'overdue' ? 'rgo-item-red' : 'rgo-item';
        html += `<div class="${cls}">• ${f.text}</div>`;
      });
      html += '</div>';
    }

    // Glossary orders from НУ
    if (data.glossary && data.glossary.length > 0) {
      html += '<div class="rgo-card"><div class="rgo-card-title">📋 Поручения НУ</div>';
      data.glossary.forEach(g => {
        const cls = g.priority === 'urgent' ? 'rgo-item-red' : 'rgo-item';
        html += `<div class="${cls}">• ${g.text}</div>`;
      });
      html += '</div>';
    }

    // Recommendation
    if (data.recommendation) {
      html += '<div class="rgo-card"><div class="rgo-card-title">💡 Рекомендация на сегодня</div>';
      html += `<div class="rgo-rec-text">${data.recommendation}</div>`;
      if (data.generated_at) {
        html += `<div class="rgo-time">Обновлено: ${new Date(data.generated_at).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'})}</div>`;
      }
      html += '</div>';
    }

    if (!html) html = '<div class="rgo-empty">Нет данных на сегодня</div>';
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="rgo-error">Ошибка загрузки</div>';
  }
}

async function loadRgoTasks() {
  const container = document.getElementById('rgo-tasks-content');
  if (!container) return;
  container.innerHTML = '<div class="rgo-loading">Загрузка...</div>';

  try {
    const data = await apiCall('/api/rgo/tasks');
    let html = '';

    // Stats bar
    html += `<div class="rgo-stats-bar">
      <div class="rgo-stat"><span class="rgo-stat-num">${data.stats.open}</span><span class="rgo-stat-label">Открыто</span></div>
      <div class="rgo-stat rgo-stat-red"><span class="rgo-stat-num">${data.stats.overdue}</span><span class="rgo-stat-label">Просрочено</span></div>
      <div class="rgo-stat rgo-stat-green"><span class="rgo-stat-num">${data.stats.closed_today}</span><span class="rgo-stat-label">Закрыто сегодня</span></div>
    </div>`;

    // Glossary
    if (data.glossary && data.glossary.length > 0) {
      html += '<div class="rgo-card"><div class="rgo-card-title">📋 Поручения НУ</div>';
      data.glossary.forEach(g => {
        html += `<div class="rgo-item-red">• ${g.text}</div>`;
      });
      html += '</div>';
    }

    // Tasks
    if (data.tasks && data.tasks.length > 0) {
      html += '<div class="rgo-card"><div class="rgo-card-title">📝 Задачи</div>';
      data.tasks.forEach(t => {
        const cls = t.status === 'overdue' ? 'rgo-task-overdue' : 'rgo-task';
        const badge = t.status === 'overdue' ? '<span class="rgo-badge-red">просрочено</span>' : '';
        html += `<div class="${cls}">
          <div class="rgo-task-text">${badge} ${t.text}</div>
          <div class="rgo-task-meta">${t.assigner || ''} ${t.due_date ? '• до ' + t.due_date : ''}</div>
          <button class="rgo-task-close" onclick="closeTask(${t.id})">✓ Выполнено</button>
        </div>`;
      });
      html += '</div>';
    } else {
      html += '<div class="rgo-empty">Нет открытых задач 🎉</div>';
    }

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="rgo-error">Ошибка загрузки</div>';
  }
}

async function loadRgoTeam() {
  const container = document.getElementById('rgo-team-content');
  if (!container) return;
  container.innerHTML = '<div class="rgo-loading">Загрузка...</div>';

  try {
    const data = await apiCall('/api/rgo/team');
    let html = '';

    // Today metrics
    html += `<div class="rgo-stats-bar">
      <div class="rgo-stat"><span class="rgo-stat-num">${data.today.messages}</span><span class="rgo-stat-label">Сообщений</span></div>
      <div class="rgo-stat"><span class="rgo-stat-num">${data.today.participants}</span><span class="rgo-stat-label">Участников</span></div>
      <div class="rgo-stat"><span class="rgo-stat-num rgo-stat-small">${data.today.last_message}</span><span class="rgo-stat-label">Посл. сообщение</span></div>
    </div>`;

    // Silent members
    if (data.silent_members && data.silent_members.length > 0) {
      html += '<div class="rgo-card"><div class="rgo-card-title">🔇 Молчат сегодня</div>';
      data.silent_members.forEach(name => {
        html += `<div class="rgo-item-yellow">• ${name}</div>`;
      });
      html += '</div>';
    }

    // Top week
    if (data.top_week && data.top_week.length > 0) {
      html += '<div class="rgo-card"><div class="rgo-card-title">🏆 Топ за неделю</div>';
      data.top_week.forEach((p, i) => {
        const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : '  ';
        html += `<div class="rgo-item">${medal} ${p.name} — <b>${p.messages}</b> сообщ.</div>`;
      });
      html += '</div>';
    }

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="rgo-error">Ошибка загрузки</div>';
  }
}

async function closeTask(taskId) {
  try {
    await apiCall(`/api/rgo/tasks/${taskId}/close`, { method: 'POST' });
    showToast('Задача закрыта');
    loadRgoTasks(); // reload
  } catch (e) {
    showToast('Ошибка');
  }
}

function openRgoSection(name) {
  ['tips', 'tasks', 'team'].forEach(s => {
    const el = document.getElementById('rgo-body-' + s);
    if (el) el.style.display = s === name ? 'block' : 'none';
  });
  document.querySelectorAll('.rgo-tile').forEach(t => t.classList.remove('active'));
  const active = document.getElementById('rgo-tile-' + name);
  if (active) active.classList.add('active');

  if (name === 'tips') loadRgoTips();
  else if (name === 'tasks') loadRgoTasks();
  else if (name === 'team') loadRgoTeam();
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
