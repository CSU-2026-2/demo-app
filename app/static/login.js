document.querySelector('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const error = document.querySelector('#login-error');
  button.disabled = true;
  error.hidden = true;
  try {
    const response = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
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
