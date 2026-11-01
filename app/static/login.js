const registration = location.pathname === '/register';
const form = document.querySelector('#login-form');
if (registration) {
  document.title = 'Регистрация — По шагам';
  document.querySelector('#auth-heading').textContent = 'Создать аккаунт';
  document.querySelector('#auth-description').textContent = 'Ваши задачи будут доступны только вам.';
  form.querySelector('button').textContent = 'Зарегистрироваться';
  form.elements.password.autocomplete = 'new-password';
  document.querySelector('#confirm-label').hidden = false;
  form.elements.confirmation.required = true;
  const link = document.querySelector('#auth-switch');
  link.href = '/login';
  link.textContent = 'Уже есть аккаунт? Войти';
}
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const error = document.querySelector('#login-error');
  button.disabled = true;
  error.hidden = true;
  try {
    if (registration && form.elements.password.value !== form.elements.confirmation.value) {
      throw new Error('Пароли не совпадают.');
    }
    const response = await fetch(registration ? '/auth/register' : '/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: form.elements.username.value, password: form.elements.password.value }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || 'Не удалось войти. Попробуйте ещё раз.');
    }
    location.replace('/');
  } catch (reason) {
    error.textContent = reason.message || 'Нет связи с сервером.';
    error.hidden = false;
  } finally { button.disabled = false; }
});
