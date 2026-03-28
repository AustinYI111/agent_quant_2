/* ===== ui-utils.js — UI Utility Functions ===== */
/* Provides: theme switching, enhanced toasts, skeleton screens,    */
/* real-time update badges, and general micro-interactions.         */
(function (global) {
  'use strict';

  /* ── Theme Manager ────────────────────────────────────────────── */
  const ThemeManager = (function () {
    const STORAGE_KEY = 'aq-theme';
    const THEMES = ['dark', 'light'];

    function get() {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && THEMES.includes(stored)) return stored;
      // Respect OS preference on first visit; default to 'dark' when no-preference
      const mq = window.matchMedia('(prefers-color-scheme: light)');
      return (mq.matches && mq.media !== 'not all') ? 'light' : 'dark';
    }

    function apply(theme) {
      if (theme === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
      } else {
        document.documentElement.removeAttribute('data-theme');
      }
    }

    function save(theme) {
      localStorage.setItem(STORAGE_KEY, theme);
    }

    function toggle() {
      const current = get();
      const next = current === 'dark' ? 'light' : 'dark';
      save(next);
      apply(next);
      _updateToggleBtn(next);
      return next;
    }

    function init() {
      const theme = get();
      apply(theme);
      _updateToggleBtn(theme);
    }

    function _updateToggleBtn(theme) {
      const btn = document.getElementById('theme-toggle-btn');
      if (!btn) return;
      const icon  = btn.querySelector('.theme-icon');
      const label = btn.querySelector('.theme-toggle-label');
      if (icon) {
        icon.classList.add('switching');
        setTimeout(() => {
          icon.textContent = theme === 'dark' ? '☀️' : '🌙';
          icon.classList.remove('switching');
        }, 150);
      }
      if (label) label.textContent = theme === 'dark' ? '亮色' : '暗色';
      btn.setAttribute('aria-label', theme === 'dark' ? '切换为亮色主题' : '切换为暗色主题');
      btn.setAttribute('data-tooltip', theme === 'dark' ? '切换亮色' : '切换暗色');
    }

    return { init, toggle, get };
  })();

  /* ── Toast Manager ────────────────────────────────────────────── */
  const ToastManager = (function () {
    const ICONS = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
    const DEFAULT_DURATION = 4000;

    function show({ type = 'info', title = '', message = '', duration = DEFAULT_DURATION } = {}) {
      const container = document.getElementById('toast-container');
      if (!container) return;

      const toast = document.createElement('div');
      toast.className = `toast ${type}`;
      toast.setAttribute('role', 'alert');
      toast.setAttribute('aria-live', 'assertive');
      toast.innerHTML = `
        <span class="toast-icon">${ICONS[type] || ICONS.info}</span>
        <div class="toast-body">
          ${title ? `<div class="toast-title">${_esc(title)}</div>` : ''}
          <div class="toast-msg">${_esc(message || title)}</div>
        </div>
      `;

      // Click to dismiss
      toast.addEventListener('click', () => _dismiss(toast));

      container.appendChild(toast);

      // Auto-dismiss
      if (duration > 0) {
        setTimeout(() => _dismiss(toast), duration);
      }

      return toast;
    }

    function _dismiss(toast) {
      if (!toast || toast.classList.contains('removing')) return;
      toast.classList.add('removing');
      toast.addEventListener('animationend', () => toast.remove(), { once: true });
      // Fallback removal
      setTimeout(() => toast.remove(), 600);
    }

    function _esc(str) {
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#x27;');
    }

    return { show };
  })();

  /* ── Skeleton Screen ──────────────────────────────────────────── */
  const SkeletonScreen = (function () {
    /**
     * Replace the innerHTML of `container` with skeleton placeholders.
     * Call restore() (returned function) to put the original content back.
     */
    function show(container, count = 1, type = 'card') {
      if (!container) return () => {};
      const original = container.innerHTML;

      const templates = {
        card: `
          <div class="skeleton skeleton-block" style="height:80px;margin-bottom:12px"></div>`,
        kpi: `
          <div style="background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:var(--radius);padding:20px">
            <div class="skeleton skeleton-text" style="width:50%"></div>
            <div class="skeleton skeleton-title"></div>
            <div class="skeleton skeleton-text" style="width:35%"></div>
          </div>`,
        row: `
          <div class="skeleton skeleton-text" style="height:40px;border-radius:4px;margin-bottom:8px"></div>`,
      };

      const tpl = templates[type] || templates.card;
      container.innerHTML = Array.from({ length: count }, () => tpl).join('');

      return function restore() {
        container.innerHTML = original;
      };
    }

    return { show };
  })();

  /* ── Update Badge ─────────────────────────────────────────────── */
  const UpdateBadge = (function () {
    let _badge = null;

    function show(text = '数据已更新') {
      hide();
      const container = document.getElementById('toast-container');
      if (!container) return;

      _badge = document.createElement('div');
      _badge.className = 'update-badge';
      _badge.textContent = '🔄 ' + text;
      container.prepend(_badge);

      setTimeout(hide, 3000);
    }

    function hide() {
      if (_badge) { _badge.remove(); _badge = null; }
    }

    return { show, hide };
  })();

  /* ── Number Counter Animation ─────────────────────────────────── */
  function animateNumber(el, targetStr) {
    if (!el) return;
    // Try to extract a numeric value from the string
    const numMatch = targetStr.replace(/,/g, '').match(/-?\d+(\.\d+)?/);
    if (!numMatch) { el.textContent = targetStr; return; }

    const target = parseFloat(numMatch[0]);
    const prefix = targetStr.slice(0, targetStr.indexOf(numMatch[0]));
    const suffix = targetStr.slice(targetStr.indexOf(numMatch[0]) + numMatch[0].length);
    const start  = 0;
    const duration = 800;
    const startTime = performance.now();

    function easeOut(t) { return 1 - Math.pow(1 - t, 3); }

    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const current = start + (target - start) * easeOut(progress);

      // Format similarly to original
      const decimals = (numMatch[0].includes('.')) ? numMatch[0].split('.')[1].length : 0;
      el.textContent = prefix + current.toLocaleString('zh-CN', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }) + suffix;

      if (progress < 1) requestAnimationFrame(step);
      else el.textContent = targetStr;
    }

    requestAnimationFrame(step);
  }

  /* ── Stagger children entrance ───────────────────────────────── */
  function staggerChildren(container, animClass = 'animate-slide-in-up', baseDelayMs = 50) {
    if (!container) return;
    const children = Array.from(container.children);
    children.forEach((child, i) => {
      child.style.animationDelay = `${i * baseDelayMs}ms`;
      child.classList.add(animClass);
    });
  }

  /* ── Page transition helper ──────────────────────────────────── */
  function pageEnter(section) {
    if (!section) return;
    section.classList.add('page-enter');
    section.addEventListener('animationend', () => section.classList.remove('page-enter'), { once: true });
  }

  /* ── Expose public API ───────────────────────────────────────── */
  global.AQUi = {
    ThemeManager,
    ToastManager,
    SkeletonScreen,
    UpdateBadge,
    animateNumber,
    staggerChildren,
    pageEnter,
  };

})(window);
