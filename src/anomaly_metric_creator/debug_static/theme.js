// Theme control for foundation UIs: dark (default), light or system.
// Load it in <head> without defer, so the stored choice applies before first paint.
// Any element with data-theme-choice="dark|light|system" becomes a control;
// the selected one gets aria-pressed="true".
(() => {
  const KEY = 'theme';
  const CHOICES = ['dark', 'light', 'system'];
  const root = document.documentElement;

  const read = () => {
    try { const v = localStorage.getItem(KEY); return CHOICES.includes(v) ? v : 'dark'; }
    catch { return 'dark'; }
  };
  const mark = (choice) => {
    document.querySelectorAll('[data-theme-choice]').forEach((el) =>
      el.setAttribute('aria-pressed', String(el.dataset.themeChoice === choice)));
  };
  const apply = (choice) => {
    root.dataset.theme = choice;
    try { localStorage.setItem(KEY, choice); } catch { /* storage blocked: choice lasts this page */ }
    mark(choice);
  };

  root.dataset.theme = read();
  document.addEventListener('DOMContentLoaded', () => mark(root.dataset.theme));
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-theme-choice]');
    if (el && CHOICES.includes(el.dataset.themeChoice)) apply(el.dataset.themeChoice);
  });
})();
