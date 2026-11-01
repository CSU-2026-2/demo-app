const $ = (selector) => document.querySelector(selector);
const state = { todos: [], filter: 'all', query: '', expanded: new Set(), editing: null, loading: false };
let toastTimer;
$('#today').textContent = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'long', weekday: 'long' }).format(new Date());

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  } catch {
    throw new Error('Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось выполнить действие. Попробуйте ещё раз.');
  }
  return response.json();
}

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}

function showError(error) {
  $('#error').textContent = error.message || 'Не удалось связаться с сервером.';
  $('#error').hidden = false;
}

async function load() {
  if (state.loading) return;
  state.loading = true;
  try {
    state.todos = (await api('/todos')).sort((a, b) => b.id - a.id);
    $('#error').hidden = true;
    render();
  } catch (error) {
    showError(error);
    if (!state.todos.length) $('#task-list').replaceChildren(element('p', 'loading', 'Не удалось загрузить задачи. Нажмите «Обновить».'));
  } finally { state.loading = false; }
}

function actionButton(text, title, action, todo, className = 'icon-button') {
  const button = element('button', className, text);
  button.type = 'button';
  button.title = title;
  button.setAttribute('aria-label', title);
  button.addEventListener('click', () => action(todo));
  return button;
}

function checkbox(checked, label, onChange) {
  const input = element('input', 'check');
  input.type = 'checkbox';
  input.checked = checked;
  input.setAttribute('aria-label', label);
  input.addEventListener('change', async () => {
    input.disabled = true;
    try { await onChange(input.checked); } catch (error) { input.checked = checked; showError(error); }
    finally { input.disabled = false; }
  });
  return input;
}

function renderCard(todo) {
  const card = element('article', `task-card${todo.completed ? ' completed' : ''}`);
  const top = element('div', 'task-top');
  top.append(checkbox(todo.completed, `Завершить задачу: ${todo.title}`, async (completed) => {
    await api(`/todos/${todo.id}`, { method: 'PUT', body: JSON.stringify({ title: todo.title, description: todo.description, completed }) });
    await load();
  }));
  const copy = element('div', 'task-copy');
  copy.append(element('h3', 'task-title', todo.title));
  if (todo.description) copy.append(element('p', 'task-description', todo.description));
  const tools = element('div', 'task-tools');
  tools.append(actionButton('✎', 'Редактировать задачу', openEditor, todo), actionButton('×', 'Удалить задачу', deleteTask, todo, 'icon-button delete-button'));
  top.append(copy, tools);
  const bottom = element('div', 'task-bottom');
  bottom.append(element('span', 'badge', todo.completed ? 'Завершена' : 'В работе'));
  const completed = todo.steps.filter((step) => step.completed).length;
  if (todo.steps.length) {
    const progress = element('span', 'progress');
    const bar = element('span');
    bar.style.width = `${100 * completed / todo.steps.length}%`;
    progress.append(bar);
    bottom.append(progress, element('span', '', `${completed}/${todo.steps.length}`));
  }
  const expanded = state.expanded.has(todo.id);
  const toggle = actionButton(`${todo.steps.length ? 'План действий' : 'Добавить план'} ${expanded ? '⌃' : '⌄'}`, 'Показать план действий', () => {
    if (expanded) state.expanded.delete(todo.id); else state.expanded.add(todo.id);
    render();
  }, todo, 'plan-toggle');
  toggle.setAttribute('aria-expanded', String(expanded));
  toggle.setAttribute('aria-controls', `plan-${todo.id}`);
  bottom.append(toggle);
  card.append(top, bottom);
  if (expanded) {
    const steps = element('div', 'steps');
    steps.id = `plan-${todo.id}`;
    steps.append(element('h3', '', 'План действий'));
    if (todo.steps.length) {
      for (const step of todo.steps) {
        const label = element('label', 'step');
        label.append(checkbox(step.completed, step.description, async (value) => {
          await api(`/todos/${todo.id}/steps/${step.id}`, { method: 'PATCH', body: JSON.stringify({ completed: value }) });
          await load();
        }), element('span', '', step.description));
        steps.append(label);
      }
    } else {
      steps.append(element('p', 'plan-empty', 'План пока не появился. Запросите шаги у помощника — они добавятся сюда автоматически.'));
      const request = actionButton('Составить план ↗', 'Запросить генерацию плана', async () => {
        request.disabled = true;
        request.textContent = 'Отправляем…';
        try {
          await api(`/todos/${todo.id}/generate`, { method: 'POST' });
          toast('Запрос отправлен. План появится после генерации.');
          await load();
        } catch (error) { showError(error); }
        finally { request.disabled = false; request.textContent = 'Составить план ↗'; }
      }, todo, 'plan-request');
      steps.append(request);
    }
    card.append(steps);
  }
  return card;
}

function render() {
  const done = state.todos.filter((todo) => todo.completed).length;
  $('#total-count').textContent = $('#nav-count').textContent = $('#heading-count').textContent = state.todos.length;
  $('#active-count').textContent = state.todos.length - done;
  $('#done-count').textContent = done;
  const visible = state.todos.filter((todo) =>
    (state.filter === 'all' || todo.completed === (state.filter === 'done')) &&
    `${todo.title} ${todo.description}`.toLocaleLowerCase().includes(state.query)
  );
  const list = $('#task-list');
  if (visible.length) { list.replaceChildren(...visible.map(renderCard)); return; }
  const empty = element('div', 'empty');
  const first = !state.todos.length;
  empty.append(element('span', 'empty-symbol', first ? '↗' : '⌕'), element('h3', '', first ? 'С чего начнём?' : 'Здесь пока ничего нет'), element('p', '', first ? 'Добавьте первую задачу. Маленькие шаги сделают большую цель понятнее.' : 'Попробуйте другой фильтр или измените поисковый запрос.'));
  if (first) empty.append(actionButton('＋ Создать первую задачу', 'Создать первую задачу', () => openEditor(), null, 'primary'));
  list.replaceChildren(empty);
}

function openEditor(todo = null) {
  state.editing = todo;
  $('#task-form').reset();
  $('#task-title').setCustomValidity('');
  $('#task-title').value = todo?.title || '';
  $('#task-form').elements.description.value = todo?.description || '';
  $('#dialog-title').textContent = todo ? 'Редактировать задачу' : 'Новая задача';
  $('#save-task').textContent = todo ? 'Сохранить' : 'Создать задачу';
  $('#form-error').hidden = true;
  $('#task-dialog').showModal();
  $('#task-title').focus();
}

async function deleteTask(todo) {
  if (!confirm(`Удалить задачу «${todo.title}» и её шаги?`)) return;
  try {
    await api(`/todos/${todo.id}`, { method: 'DELETE' });
    state.expanded.delete(todo.id);
    await load();
    toast('Задача удалена');
  } catch (error) { showError(error); }
}

$('#task-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const title = $('#task-title').value.trim();
  if (!title) { $('#task-title').setCustomValidity('Введите название задачи'); $('#task-title').reportValidity(); return; }
  const todo = state.editing;
  const body = { title, description: $('#task-form').elements.description.value.trim(), completed: todo?.completed || false };
  $('#save-task').disabled = true;
  $('#form-error').hidden = true;
  try {
    const result = await api(todo ? `/todos/${todo.id}` : '/todos', { method: todo ? 'PUT' : 'POST', body: JSON.stringify(body) });
    if (!todo) state.expanded.add(result.id);
    $('#task-dialog').close();
    await load();
    toast(todo ? 'Изменения сохранены' : 'Задача добавлена');
  } catch (error) {
    $('#form-error').textContent = error.message || 'Сервер недоступен. Попробуйте ещё раз.';
    $('#form-error').hidden = false;
  } finally { $('#save-task').disabled = false; }
});
$('#task-title').addEventListener('input', () => $('#task-title').setCustomValidity(''));
$('#new-task').addEventListener('click', () => openEditor());
$('#close-dialog').addEventListener('click', () => $('#task-dialog').close());
$('#cancel-dialog').addEventListener('click', () => $('#task-dialog').close());
$('#refresh').addEventListener('click', load);
$('#search').addEventListener('input', (event) => { state.query = event.target.value.toLocaleLowerCase().trim(); render(); });
document.querySelectorAll('[data-filter]').forEach((button) => button.addEventListener('click', () => {
  state.filter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
  render();
}));
load();
setInterval(() => { if (!document.hidden && !$('#task-dialog').open && state.todos.some((todo) => !todo.steps.length && !todo.completed)) load(); }, 5000);
