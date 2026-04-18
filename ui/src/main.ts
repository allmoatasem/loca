import { mount } from 'svelte';
import './lib/tokens.css';
import 'katex/dist/katex.min.css';
import App from './App.svelte';

// Apply the saved theme before first paint so the app doesn't flash light
// when the user has picked Dark. PreferencesView keeps its own effect in
// sync, so this just covers the boot window.
(() => {
  const saved = localStorage.getItem('loca-theme');
  if (saved === 'light' || saved === 'dark') {
    document.documentElement.dataset.theme = saved;
    document.documentElement.style.colorScheme = saved;
  }
})();

const app = mount(App, {
  target: document.getElementById('app')!,
});

export default app;
