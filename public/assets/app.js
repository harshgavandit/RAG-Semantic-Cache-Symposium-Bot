const form = document.querySelector('#chat-form');
const input = document.querySelector('#question');
const send = document.querySelector('#send');
const messages = document.querySelector('#messages');
const errorBox = document.querySelector('#error-message');
const emptyState = document.querySelector('#empty-state');
let busy = false;

function addMessage(role, content, result = {}) {
  emptyState.hidden = true;
  const article = document.createElement('article');
  article.className = `message ${role}`;
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = role === 'user' ? 'You' : 'Symposium';
  if (result.cache_hit) {
    const badge = document.createElement('span');
    badge.className = 'cache-badge';
    badge.textContent = 'Cached answer';
    label.append(badge);
  }
  const text = document.createElement('div');
  text.className = 'message-content';
  text.textContent = content;
  article.append(label, text);
  if (result.sources?.length) {
    const details = document.createElement('details');
    details.className = 'sources';
    const summary = document.createElement('summary');
    summary.textContent = 'Document sources';
    details.append(summary);
    for (const page of result.sources) {
      const source = document.createElement('span');
      source.className = 'source-page';
      source.textContent = page;
      details.append(source);
    }
    article.append(details);
  }
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const question = input.value.trim();
  if (busy || !question) return;
  busy = true;
  send.disabled = true;
  input.disabled = true;
  send.setAttribute('aria-label', 'Waiting for answer');
  errorBox.hidden = true;
  addMessage('user', question);
  input.value = '';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 55000);
  try {
    const response = await fetch('/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question}), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) {
      if (response.status === 429) throw new Error(`You've reached the question limit. Please try again in ${response.headers.get('Retry-After') || '60'} seconds.`);
      throw new Error(typeof result.detail === 'string' ? result.detail : 'Please enter a valid question of up to 4,000 characters.');
    }
    addMessage('assistant', result.answer, result);
  } catch (error) {
    errorBox.textContent = error.name === 'AbortError' ? 'This answer is taking a little longer. Please try again.' : error.message === 'Failed to fetch' ? 'Could not connect. Check your connection and try again.' : error.message;
    errorBox.hidden = false;
    input.value = question;
  } finally {
    clearTimeout(timeout);
    busy = false;
    send.disabled = false;
    input.disabled = false;
    send.setAttribute('aria-label', 'Send question');
    input.focus();
  }
});

input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
});
document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => {
  if (!busy) { input.value = button.dataset.question; form.requestSubmit(); }
}));
document.querySelector('#clear-chat').addEventListener('click', () => {
  if (busy) return;
  messages.querySelectorAll('.message').forEach(message => message.remove());
  emptyState.hidden = false;
  errorBox.hidden = true;
  input.value = '';
  input.focus();
});
fetch('/health').then(response => {
  const status = document.querySelector('#service-status');
  status.classList.add(response.ok ? 'ready' : 'offline');
  status.lastChild.textContent = response.ok ? 'Ready to help' : 'Temporarily unavailable';
}).catch(() => {
  const status = document.querySelector('#service-status');
  status.classList.add('offline');
  status.lastChild.textContent = 'Check connection';
});
