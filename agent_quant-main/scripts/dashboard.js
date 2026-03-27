/* ===== dashboard.js ===== */
(function () {
  'use strict';

  // ── Configuration ────────────────────────────────────────────────────
  const CONFIG = {
    apiBase:         'http://localhost:5000',
    staticDataUrl:   './data/sample-trades.json',
    refreshInterval: 60000,  // 60 sec polling interval (ms)
    pageSize: 8,
  };

  // Chart.js colour palette
  const COLORS = {
    trend:    '#3b82f6',
    meanrev:  '#f59e0b',
    fusion:   '#10b981',
    fusionML: '#a855f7',
    up:       'rgba(74, 222, 128, 0.8)',
    down:     'rgba(248, 113, 113, 0.8)',
    upFill:   'rgba(74, 222, 128, 0.1)',
    downFill: 'rgba(248, 113, 113, 0.1)',
  };

  const PAGE_META = {
    dashboard: { title: '交易仪表板',    subtitle: '多策略量化回测结果概览' },
    analysis:  { title: '收益分析',      subtitle: '收益曲线与回撤详情' },
    strategy:  { title: '策略对比',      subtitle: '各策略绩效指标横向对比' },
    trades:    { title: '历史交易记录',  subtitle: '所有已平仓/持仓交易明细' },
    config:    { title: '回测参数配置',  subtitle: '设置股票代码、日期范围及策略参数' },
  };

  // ── State ───────────────────────────────────────────────────────────────
  let state = {
    data: null,
    activeStrategy: 'all',
    currentPage: 1,
    sortKey: 'id',
    sortDir: 'desc',
    searchQuery: '',
    charts: {},
    refreshTimer: null,
    activePage: 'dashboard',
    apiAvailable: false,
    backtestPollTimer: null,
  };

  // ── DOM helpers ──────────────────────────────────────────────────────────
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

  // ── Date Validation ──────────────────────────────────────────────────────
  function isValidDate(year, month, day) {
    if (month < 1 || month > 12 || day < 1 || day > 31) return false;
    const d = new Date(year, month - 1, day);
    return d.getFullYear() === year && d.getMonth() === month - 1 && d.getDate() === day;
  }

  function fmt(val, decimals = 2) {
    if (val == null) return '—';
    return Number(val).toLocaleString('zh-CN', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  function fmtPct(val, decimals = 2) {
    if (val == null) return '—';
    const v = (val * 100).toFixed(decimals);
    return (val >= 0 ? '+' : '') + v + '%';
  }

  function fmtMoney(val) {
    if (val == null) return '—';
    if (Math.abs(val) >= 10000) return fmt(val / 10000, 2) + '万';
    return fmt(val, 2);
  }

  // ── API Detection ────────────────────────────────────────────────────────
  async function detectApi() {
    const dot  = $('#api-status-dot');
    const text = $('#api-status-text');
    const urlEl = $('#api-base-url');
    if (urlEl) urlEl.textContent = CONFIG.apiBase;

    try {
      let signal;
      if (typeof AbortSignal.timeout === 'function') {
        signal = AbortSignal.timeout(2000);
      } else {
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 2000);
        signal = controller.signal;
      }
      const res = await fetch(CONFIG.apiBase + '/api/health', { signal });
      if (res.ok) {
        state.apiAvailable = true;
        if (dot)  dot.style.background = '#4ade80';
        if (text) text.textContent = 'API 已连接';
        return true;
      }
    } catch (_) { /* fall through */ }
    state.apiAvailable = false;
    if (dot)  dot.style.background = '#f87171';
    if (text) text.textContent = '离线（静态数据）';
    return false;
  }

  // ── Data Loading ─────────────────────────────────────────────────────────
  async function loadData() {
    // 1. Try API /api/results
    if (state.apiAvailable) {
      try {
        const res = await fetch(CONFIG.apiBase + '/api/results');
        if (res.ok) return await res.json();
      } catch (_) { /* fall through */ }
    }
    // 2. Fall back to static sample file
    try {
      const res = await fetch(CONFIG.staticDataUrl);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) {
      console.warn('Failed to fetch data.', e.message);
      return null;
    }
  }

  // ── Page Navigation ─────────────────────────────────────────────────────
  function switchPage(pageId) {
    if (!PAGE_META[pageId]) return;
    state.activePage = pageId;

    // Update nav items
    $$('.nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.page === pageId);
    });

    // Show/hide page sections
    $$('.page-section').forEach(sec => {
      sec.classList.toggle('hidden', sec.id !== 'page-' + pageId);
    });

    // Update header
    const meta = PAGE_META[pageId];
    const titleEl = $('#page-title');
    const subEl   = $('#page-subtitle');
    if (titleEl) titleEl.textContent = meta.title;
    if (subEl)   subEl.textContent   = meta.subtitle;

    // Render page-specific charts that may not have been initialised yet
    if (state.data) {
      if (pageId === 'analysis')  { renderAnalysisCharts(state.data); renderMonthlyHeatmap(state.data); }
      if (pageId === 'strategy')  { renderStrategyCards(state.data); renderStrategyComparisonChart(state.data); }
      if (pageId === 'trades')    { renderTradesTable(state.data); renderTradeStatsChart(state.data); }
      if (pageId === 'dashboard') renderRiskMetrics(state.data);
    }
  }

  // ── Render KPI Cards ────────────────────────────────────────────────────
  function renderKPIs(data) {
    const active = getActiveStrategy(data);
    if (!active) return;

    const initial = data.summary.initialCapital;
    const finalEquity = initial * (1 + active.totalReturn);
    const pnl = finalEquity - initial;

    setKPI('kpi-total-return', fmtPct(active.totalReturn),
      active.totalReturn >= 0 ? 'positive' : 'negative',
      (active.totalReturn >= 0 ? '▲' : '▼') + ' 相对初始资金');

    setKPI('kpi-annual-return', fmtPct(active.annualReturn),
      active.annualReturn >= 0 ? 'positive' : 'negative',
      '年化收益率');

    setKPI('kpi-max-drawdown', fmtPct(active.maxDrawdown, 2),
      'negative', '最大回撤');

    setKPI('kpi-sharpe', fmt(active.sharpe, 3),
      active.sharpe >= 0.5 ? 'positive' : active.sharpe >= 0 ? 'neutral' : 'negative',
      '夏普比率');

    setKPI('kpi-pnl', (pnl >= 0 ? '+' : '') + fmtMoney(pnl),
      pnl >= 0 ? 'positive' : 'negative',
      '总盈亏 (¥)');

    setKPI('kpi-trades', active.numTrades,
      'neutral', '交易笔数');
  }

  function setKPI(id, value, cls, sub) {
    const el = $('#' + id);
    if (!el) return;
    el.querySelector('.kpi-value').textContent = value;
    const delta = el.querySelector('.kpi-delta');
    delta.textContent = sub;
    delta.className = 'kpi-delta ' + cls;
  }

  function getActiveStrategy(data) {
    const strats = data.summary.strategies;
    if (state.activeStrategy === 'all') {
      return strats.reduce((a, b) => a.totalReturn > b.totalReturn ? a : b);
    }
    return strats.find(s => s.name === state.activeStrategy) || strats[0];
  }

  // ── Equity Curve Chart (dashboard overview) ──────────────────────────
  function renderEquityChart(data) {
    const ctx = $('#equity-chart');
    if (!ctx) return;
    const ds = buildEquityDatasets(data);
    const labels = data.equityCurves.labels;

    if (state.charts.equity) {
      state.charts.equity.data.labels = labels;
      state.charts.equity.data.datasets = ds;
      state.charts.equity.update('active');
      return;
    }
    state.charts.equity = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets: ds },
      options: equityChartOptions(),
    });
  }

  function buildEquityDatasets(data) {
    const ec = data.equityCurves;
    const labels = ec.labels || [];
    return [
      { key: 'Trend-only',       label: 'Trend-only',       color: COLORS.trend },
      { key: 'MeanRev-only',     label: 'MeanRev-only',     color: COLORS.meanrev },
      { key: 'Fusion(no-ML)',    label: 'Fusion(no-ML)',    color: COLORS.fusion },
      { key: 'Fusion(+ML-veto)', label: 'Fusion(+ML-veto)', color: COLORS.fusionML },
    ].filter(d => ec[d.key] && ec[d.key].length).map(d => {
      const rawData = ec[d.key] || [];
      // Align dataset length to labels length to prevent chart misalignment;
      // when lengths already match and there are no nulls, take the fast path.
      const alignedData = (rawData.length === labels.length && rawData.every(v => v != null))
        ? rawData.map(v => +v.toFixed(2))
        : labels.map((_, i) => rawData[i] != null ? +rawData[i].toFixed(2) : null);
      return {
        label: d.label,
        data: alignedData,
        borderColor: d.color,
        backgroundColor: d.color + '18',
        borderWidth: 2.5,
        pointRadius: 0,
        pointHoverRadius: 5,
        tension: 0.2,
        fill: false,
      };
    });
  }

  function equityChartOptions() {
    return {
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: 2.4,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1e293b',
          borderColor: '#334155',
          borderWidth: 1,
          titleColor: '#f1f5f9',
          bodyColor: '#94a3b8',
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ¥${ctx.parsed.y.toLocaleString('zh-CN')}`,
          },
        },
      },
      scales: {
        x: { 
          grid: { color: '#1e293b', drawBorder: false },
          ticks: { color: '#64748b', maxTicksLimit: 12 }
        },
        y: {
          grid: { color: '#1e293b', drawBorder: false },
          ticks: { color: '#64748b', callback: v => '¥' + (v / 1000).toFixed(0) + 'k' },
        },
      },
    };
  }

  // ── Analysis page charts ────────────────────────────────────────────────
  function renderAnalysisCharts(data) {
    renderEquityFull(data);
    renderDrawdownChart(data);
  }

  function renderEquityFull(data) {
    const ctx = $('#equity-chart-full');
    if (!ctx) return;
    const ds = buildEquityDatasets(data);
    const labels = data.equityCurves.labels;

    if (state.charts.equityFull) {
      state.charts.equityFull.data.labels = labels;
      state.charts.equityFull.data.datasets = ds;
      state.charts.equityFull.update('active');
      return;
    }
    const opts = equityChartOptions();
    opts.maintainAspectRatio = false;
    opts.plugins.legend = {
      display: true,
      position: 'top',
      labels: { color: '#94a3b8', boxWidth: 12, padding: 16 },
    };
    state.charts.equityFull = new Chart(ctx, { type: 'line', data: { labels, datasets: ds }, options: opts });
  }

  function renderDrawdownChart(data) {
    const ctx = $('#drawdown-chart');
    if (!ctx) return;

    const ec = data.equityCurves;
    const labels = ec.labels;
    const stratKeys = ['Trend-only', 'MeanRev-only', 'Fusion(no-ML)', 'Fusion(+ML-veto)'];
    const colors = [COLORS.trend, COLORS.meanrev, COLORS.fusion, COLORS.fusionML];

    const datasets = stratKeys.filter(k => ec[k] && ec[k].length).map((k, i) => {
      const equity = ec[k];
      let peak = equity[0];
      const dd = equity.map(v => {
        peak = Math.max(peak, v);
        return peak > 0 ? +((v / peak - 1) * 100).toFixed(3) : 0;
      });
      return {
        label: k, data: dd, borderColor: colors[i],
        backgroundColor: colors[i] + '22', borderWidth: 1.5,
        pointRadius: 0, tension: 0.2, fill: true,
      };
    });

    if (state.charts.drawdown) {
      state.charts.drawdown.data.labels = labels;
      state.charts.drawdown.data.datasets = datasets;
      state.charts.drawdown.update('active');
      return;
    }
    state.charts.drawdown = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'top', labels: { color: '#94a3b8', boxWidth: 10, padding: 12 } },
          tooltip: {
            backgroundColor: '#1e293b', borderColor: '#334155', borderWidth: 1,
            titleColor: '#f1f5f9', bodyColor: '#94a3b8',
            callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}%` },
          },
        },
        scales: {
          x: { grid: { color: '#1e293b' }, ticks: { color: '#64748b', maxTicksLimit: 8 } },
          y: {
            grid: { color: '#1e293b' },
            ticks: { color: '#64748b', callback: v => v + '%' },
          },
        },
      },
    });
  }

  // ── Win/Loss Pie Chart ───────────────────────────────────────────────
  function renderWinLossChart(data) {
    const ctx = $('#winloss-chart');
    if (!ctx) return;

    const trades = filterTrades(data.trades);
    const wins   = trades.filter(t => t.pnl > 0).length;
    const losses = trades.filter(t => t.pnl <= 0).length;
    const winRate = trades.length ? wins / trades.length : 0;

    const winLabel = $('#winrate-label');
    if (winLabel) winLabel.textContent = (winRate * 100).toFixed(1) + '%';

    if (state.charts.winloss) {
      state.charts.winloss.data.datasets[0].data = [wins, losses];
      state.charts.winloss.update('active');
      return;
    }
    state.charts.winloss = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['盈利', '亏损'],
        datasets: [{
          data: [wins, losses],
          backgroundColor: ['rgba(74, 222, 128, 0.8)', 'rgba(248, 113, 113, 0.8)'],
          borderColor: ['#4ade80', '#f87171'],
          borderWidth: 1,
          hoverOffset: 4,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: true, cutout: '72%',
        plugins: {
          legend: { position: 'bottom', labels: { color: '#94a3b8', boxWidth: 12, padding: 16 } },
          tooltip: { backgroundColor: '#1e293b', borderColor: '#334155', borderWidth: 1, titleColor: '#f1f5f9', bodyColor: '#94a3b8' },
        },
      },
    });
  }

  // ── Strategy Cards ───────────────────────────────────────────────────
  function renderStrategyCards(data) {
    const container = $('#strategy-cards');
    if (!container) return;

    const best = data.summary.strategies.reduce(
      (a, b) => a.totalReturn > b.totalReturn ? a : b
    );

    container.innerHTML = data.summary.strategies.map(s => {
      const isBest  = s.name === best.name;
      const retCls  = s.totalReturn >= 0 ? 'pos' : 'neg';
      const shCls   = s.sharpe >= 0.5 ? 'pos' : s.sharpe >= 0 ? 'neutral' : 'neg';
      return `
        <div class="strategy-card ${isBest ? 'best' : ''}">
          <div class="strategy-name">
            <span>${s.name}</span>
            ${isBest ? '<span class="badge badge-success">最优</span>' : ''}
            ${s.totalReturn < 0 ? '<span class="badge badge-danger">亏损</span>' : ''}
          </div>
          <div class="strategy-metrics">
            <div class="metric-item"><div class="metric-label">总收益率</div><div class="metric-value ${retCls}">${fmtPct(s.totalReturn)}</div></div>
            <div class="metric-item"><div class="metric-label">年化收益</div><div class="metric-value ${retCls}">${fmtPct(s.annualReturn)}</div></div>
            <div class="metric-item"><div class="metric-label">最大回撤</div><div class="metric-value neg">${fmtPct(s.maxDrawdown)}</div></div>
            <div class="metric-item"><div class="metric-label">夏普比率</div><div class="metric-value ${shCls}">${fmt(s.sharpe, 3)}</div></div>
            <div class="metric-item"><div class="metric-label">交易笔数</div><div class="metric-value neutral">${s.numTrades}</div></div>
            <div class="metric-item"><div class="metric-label">终值</div><div class="metric-value ${retCls}">¥${fmtMoney(data.summary.initialCapital * (1 + s.totalReturn))}</div></div>
          </div>
        </div>`;
    }).join('');
  }

  // ── Trades Table ─────────────────────────���───────────────────────────
  function filterTrades(trades) {
    let list = trades;
    if (state.activeStrategy !== 'all') {
      list = list.filter(t => t.strategy === state.activeStrategy);
    }
    if (state.searchQuery) {
      const q = state.searchQuery.toLowerCase();
      list = list.filter(t =>
        (t.symbol  || '').toLowerCase().includes(q) ||
        (t.strategy|| '').toLowerCase().includes(q) ||
        (t.direction || '').includes(q)
      );
    }
    list = [...list].sort((a, b) => {
      const va = a[state.sortKey];
      const vb = b[state.sortKey];
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return state.sortDir === 'asc' ? cmp : -cmp;
    });
    return list;
  }

  function renderTradesTable(data) {
    const trades = filterTrades(data.trades);
    const total  = trades.length;
    const pages  = Math.ceil(total / CONFIG.pageSize);
    state.currentPage = Math.min(state.currentPage, pages || 1);
    const start = (state.currentPage - 1) * CONFIG.pageSize;
    const page  = trades.slice(start, start + CONFIG.pageSize);

    const tbody = $('#trades-tbody');
    if (!tbody) return;

    tbody.innerHTML = page.map(t => {
      const dirCls = t.direction === '买入' ? 'direction-buy' : 'direction-sell';
      const pnlCls = (t.pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
      const retCls = (t.returnPct || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
      return `
        <tr>
          <td class="td-main">${t.time}</td>
          <td class="td-main">${t.symbol}</td>
          <td><small style="color:var(--text-dim)">${t.strategy}</small></td>
          <td><span class="direction-badge ${dirCls}">${t.direction}</span></td>
          <td>${fmt(t.quantity, 0)}</td>
          <td>${fmt(t.entryPrice)}</td>
          <td>${t.exitPrice != null ? fmt(t.exitPrice) : '—'}</td>
          <td class="${pnlCls}">${(t.pnl || 0) >= 0 ? '+' : ''}${fmt(t.pnl)}</td>
          <td class="${retCls}">${fmtPct(t.returnPct)}</td>
          <td><span class="status-closed">${t.status}</span></td>
        </tr>`;
    }).join('');

    const infoEl = $('#trades-info');
    if (infoEl) infoEl.textContent = `共 ${total} 条，第 ${state.currentPage}/${pages || 1} 页`;

    const paginationEl = $('#pagination-btns');
    if (paginationEl) {
      let html = `<button class="page-btn" id="page-prev" ${state.currentPage <= 1 ? 'disabled' : ''}>‹</button>`;
      for (let i = 1; i <= pages; i++) {
        html += `<button class="page-btn ${i === state.currentPage ? 'active' : ''}" data-page="${i}">${i}</button>`;
      }
      html += `<button class="page-btn" id="page-next" ${state.currentPage >= pages ? 'disabled' : ''}>›</button>`;
      paginationEl.innerHTML = html;

      paginationEl.querySelectorAll('[data-page]').forEach(btn => {
        btn.addEventListener('click', () => {
          state.currentPage = parseInt(btn.dataset.page);
          renderTradesTable(state.data);
        });
      });
      const prev = $('#page-prev');
      const next = $('#page-next');
      if (prev) prev.addEventListener('click', () => {
        if (state.currentPage > 1) { state.currentPage--; renderTradesTable(state.data); }
      });
      if (next) next.addEventListener('click', () => {
        if (state.currentPage < pages) { state.currentPage++; renderTradesTable(state.data); }
      });
    }
  }

  // ── Table Sorting ────────────────────────────────────────────────────
  function initTableSort() {
    $$('thead th[data-sort]').forEach(th => {
      th.addEventListener('click', () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          state.sortKey = key;
          state.sortDir = 'desc';
        }
        $$('thead th[data-sort]').forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
        th.classList.add('sort-' + state.sortDir);
        if (state.data) renderTradesTable(state.data);
      });
    });
  }

  // ── Full Render ──────────────────────────────────────────────────────
  function render(data) {
    renderKPIs(data);
    renderEquityChart(data);
    renderWinLossChart(data);
    renderRiskMetrics(data);
    if (state.activePage === 'analysis')  { renderAnalysisCharts(data); renderMonthlyHeatmap(data); }
    if (state.activePage === 'strategy')  { renderStrategyCards(data); renderStrategyComparisonChart(data); }
    if (state.activePage === 'trades')    { renderTradesTable(data); renderTradeStatsChart(data); }
    renderStrategyCards(data);
    $('#last-update').textContent = new Date().toLocaleTimeString('zh-CN');
  }

  // ── Toast ────────────────────────────────────────────────────────────
  function showToast(msg, type = 'success') {
    const container = $('#toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  // ── Refresh ──────────────────────────────────────────────────────────
  async function refresh(silent = false) {
    if (!silent) showToast('正在刷新数据…', 'success');
    await detectApi();
    const fresh = await loadData();
    if (fresh) {
      state.data = fresh;
      render(state.data);
      if (!silent) showToast('数据已更新', 'success');
    } else {
      if (!silent) showToast('数据加载失败，显示缓存', 'error');
    }
  }

  // ── Risk Metrics ─────────────────────────────────────────────────────
  function renderRiskMetrics(data) {
    const container = $('#risk-metrics-grid');
    if (!container) return;
    const active = getActiveStrategy(data);
    if (!active) return;

    const metrics = [
      {
        label: 'Sortino 比率',
        value: active.sortino != null ? fmt(active.sortino, 3) : '—',
        cls:   (active.sortino || 0) >= 1 ? 'positive' : (active.sortino || 0) >= 0 ? 'neutral' : 'negative',
        icon:  '📐',
        desc:  '下行风险调整后年化收益',
      },
      {
        label: 'Calmar 比率',
        value: active.calmar != null ? fmt(active.calmar, 3) : '—',
        cls:   (active.calmar || 0) >= 0.5 ? 'positive' : 'neutral',
        icon:  '⚡',
        desc:  '年化收益 / 最大回撤',
      },
      {
        label: '最大连亏笔数',
        value: active.maxConsecutiveLosses != null ? active.maxConsecutiveLosses : '—',
        cls:   (active.maxConsecutiveLosses || 0) <= 3 ? 'positive'
                : (active.maxConsecutiveLosses || 0) <= 5 ? 'neutral' : 'negative',
        icon:  '📛',
        desc:  '最长连续亏损交易笔数',
      },
    ];

    container.innerHTML = metrics.map(m => `
      <div class="risk-metric-card">
        <div class="risk-metric-icon">${m.icon}</div>
        <div class="risk-metric-body">
          <div class="risk-metric-label">${m.label}</div>
          <div class="risk-metric-value ${m.cls}">${m.value}</div>
          <div class="risk-metric-desc">${m.desc}</div>
        </div>
      </div>`).join('');
  }

  // ── Monthly Returns Heatmap ─────────────────────────────────────────
  function computeMonthlyReturns(data) {
    const labels = data.equityCurves.labels || [];
    const result = {};
    ['Trend-only', 'MeanRev-only', 'Fusion(no-ML)', 'Fusion(+ML-veto)'].forEach(key => {
      const equity = data.equityCurves[key];
      if (!equity || !equity.length) return;
      const monthly = {}; // {YYYY-MM: [firstVal, lastVal]}
      labels.forEach((label, i) => {
        if (equity[i] == null) return;
        const ym = label.substring(0, 7);
        if (!monthly[ym]) monthly[ym] = [equity[i], equity[i]];
        else monthly[ym][1] = equity[i];
      });
      result[key] = {};
      Object.entries(monthly).forEach(([ym, [start, end]]) => {
        result[key][ym] = start > 0 ? (end - start) / start : 0;
      });
    });
    return result;
  }

  function renderMonthlyHeatmap(data) {
    const container = $('#monthly-heatmap');
    if (!container) return;

    const strategyKey = state.activeStrategy === 'all'
      ? data.summary.strategies.reduce((a, b) => a.totalReturn > b.totalReturn ? a : b).name
      : state.activeStrategy;

    const allMonthly = computeMonthlyReturns(data);
    const returns    = allMonthly[strategyKey];

    if (!returns || !Object.keys(returns).length) {
      container.innerHTML = '<p style="color:var(--text-dim);text-align:center;padding:2rem">暂无月度收益数据</p>';
      return;
    }

    const years = [...new Set(Object.keys(returns).map(ym => ym.substring(0, 4)))].sort();
    const monthNames = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];

    let html = `<div class="heatmap-wrapper">
      <div class="heatmap-strategy-label">${strategyKey} — 月度收益</div>
      <table class="heatmap-table"><thead><tr>
        <th>年份</th>${monthNames.map(m => `<th>${m}</th>`).join('')}<th>全年合计</th>
      </tr></thead><tbody>`;

    years.forEach(year => {
      let compounded = 1;
      const cells = Array.from({ length: 12 }, (_, i) => {
        const month = String(i + 1).padStart(2, '0');
        const ret   = returns[`${year}-${month}`];
        if (ret === undefined) return '<td class="heatmap-cell heatmap-empty">—</td>';
        compounded *= (1 + ret);
        const pct = (ret * 100).toFixed(1);
        const cls = ret > 0.03  ? 'heatmap-strong-pos'
                  : ret > 0     ? 'heatmap-pos'
                  : ret > -0.03 ? 'heatmap-neg'
                  :               'heatmap-strong-neg';
        return `<td class="heatmap-cell ${cls}" title="${year}-${month}: ${pct}%">${pct > 0 ? '+' : ''}${pct}%</td>`;
      });
      const yearRet = compounded - 1;
      const yearPct = (yearRet * 100).toFixed(1);
      const yearCls = yearRet >= 0 ? 'heatmap-year-pos' : 'heatmap-year-neg';
      html += `<tr><td class="heatmap-year">${year}</td>${cells.join('')}
        <td class="heatmap-cell heatmap-year-total ${yearCls}">${yearRet >= 0 ? '+' : ''}${yearPct}%</td></tr>`;
    });

    html += '</tbody></table></div>';
    container.innerHTML = html;
  }

  // ── Trade P&L Distribution Chart ────────────────────────────────────
  function renderTradeStatsChart(data) {
    const ctx = $('#trade-pnl-chart');
    if (!ctx) return;

    const trades = filterTrades(data.trades).filter(t => t.status === '已平仓' && t.pnl != null && t.pnl !== 0);
    if (!trades.length) {
      if (state.charts.tradePnl) { state.charts.tradePnl.destroy(); delete state.charts.tradePnl; }
      return;
    }

    const pnls   = trades.map(t => t.pnl);
    const minVal = Math.min(...pnls);
    const maxVal = Math.max(...pnls);
    const range  = maxVal - minVal || 1;
    const BINS   = 12;
    const step   = range / BINS;

    const counts = Array(BINS).fill(0);
    const labels = [];
    for (let i = 0; i < BINS; i++) {
      const lo = minVal + i * step;
      const hi = minVal + (i + 1) * step;
      labels.push(Math.abs(lo) >= 10000 ? `${(lo / 10000).toFixed(1)}万` : lo.toFixed(0));
      counts[i] = pnls.filter(p => p >= lo && (i === BINS - 1 ? p <= hi : p < hi)).length;
    }
    const colors = Array.from({ length: BINS }, (_, i) =>
      (minVal + (i + 0.5) * step) >= 0 ? 'rgba(74,222,128,0.75)' : 'rgba(248,113,113,0.75)'
    );

    if (state.charts.tradePnl) {
      state.charts.tradePnl.data.labels = labels;
      state.charts.tradePnl.data.datasets[0].data   = counts;
      state.charts.tradePnl.data.datasets[0].backgroundColor = colors;
      state.charts.tradePnl.update('active');
      return;
    }
    state.charts.tradePnl = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets: [{ label: '交易次数', data: counts, backgroundColor: colors, borderRadius: 3 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1e293b', borderColor: '#334155', borderWidth: 1,
            titleColor: '#f1f5f9', bodyColor: '#94a3b8',
            callbacks: { label: item => ` ${item.raw} 笔交易` },
          },
        },
        scales: {
          x: { grid: { color: '#1e293b' }, ticks: { color: '#64748b' } },
          y: { grid: { color: '#1e293b' }, ticks: { color: '#64748b', stepSize: 1 } },
        },
      },
    });
  }

  // ── Strategy Comparison Bar Chart ────────────────────────────────────
  function renderStrategyComparisonChart(data) {
    const ctx = $('#strategy-comparison-chart');
    if (!ctx) return;

    const strategies = data.summary.strategies;
    const labels     = strategies.map(s => s.name);

    const datasets = [
      {
        label: '总收益率 (%)',
        data: strategies.map(s => +((s.totalReturn || 0) * 100).toFixed(2)),
        backgroundColor: 'rgba(59,130,246,0.75)',
      },
      {
        label: '夏普比率',
        data: strategies.map(s => +(s.sharpe || 0).toFixed(3)),
        backgroundColor: 'rgba(16,185,129,0.75)',
      },
      {
        label: '最大回撤 (%)',
        data: strategies.map(s => +((s.maxDrawdown || 0) * 100).toFixed(2)),
        backgroundColor: 'rgba(248,113,113,0.75)',
      },
    ];

    if (state.charts.stratComparison) {
      state.charts.stratComparison.data.labels = labels;
      state.charts.stratComparison.data.datasets = datasets;
      state.charts.stratComparison.update('active');
      return;
    }
    state.charts.stratComparison = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'top', labels: { color: '#94a3b8', boxWidth: 12, padding: 14 } },
          tooltip: {
            backgroundColor: '#1e293b', borderColor: '#334155', borderWidth: 1,
            titleColor: '#f1f5f9', bodyColor: '#94a3b8',
          },
        },
        scales: {
          x: { grid: { color: '#1e293b' }, ticks: { color: '#64748b' } },
          y: { grid: { color: '#1e293b' }, ticks: { color: '#64748b' } },
        },
      },
    });
  }

  // ── CSV Export ───────────────────────────────────────────────────────
  function exportTradesToCSV() {
    if (!state.data) { showToast('暂无数据可导出', 'error'); return; }
    const trades = filterTrades(state.data.trades);
    const headers = ['序号','时间','入场时间','品种','策略','方向','数量','入场价','出场价','盈亏(¥)','收益率','状态'];
    const rows = trades.map(t => [
      t.id, t.time, t.entryTime || t.time, t.symbol, t.strategy,
      t.direction, t.quantity, t.entryPrice,
      t.exitPrice != null ? t.exitPrice : '',
      t.pnl != null ? t.pnl.toFixed(2) : '',
      t.returnPct != null ? (t.returnPct * 100).toFixed(4) + '%' : '',
      t.status,
    ]);
    const csv = [headers, ...rows]
      .map(row => row.map(c => `"${String(c ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('✅ 交易记录已导出 CSV', 'success');
  }

  // ── Date Range Presets ───────────────────────────────────────────────
  function initDatePresets() {
    $$('.preset-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const preset  = btn.dataset.preset;
        const endEl   = $('#f-end-date');
        const startEl = $('#f-start-date');
        if (!endEl || !startEl) return;

        const endDate = endEl.value ? new Date(endEl.value) : new Date();
        if (isNaN(endDate)) return;

        let startDate;
        if (preset === '1y')  { startDate = new Date(endDate); startDate.setFullYear(endDate.getFullYear() - 1); }
        if (preset === '3y')  { startDate = new Date(endDate); startDate.setFullYear(endDate.getFullYear() - 3); }
        if (preset === '5y')  { startDate = new Date(endDate); startDate.setFullYear(endDate.getFullYear() - 5); }
        if (preset === 'all') { startDate = new Date('2000-01-01'); }

        if (startDate) {
          startEl.value = startDate.toISOString().slice(0, 10);
        }
      });
    });
  }

  // ── Backtest Form ────────────────────────────────────────────────────
  function initBacktestForm() {
    const form = $('#backtest-form');
    if (!form) return;

    form.addEventListener('submit', async e => {
      e.preventDefault();

      if (!state.apiAvailable) {
        showToast('❌ 未检测到 API 服务，请先启动 api_server.py', 'error');
        return;
      }

      const strategies = [];
      if ($('#f-s-trend').checked)    strategies.push('Trend-only');
      if ($('#f-s-mr').checked)       strategies.push('MeanRev-only');
      if ($('#f-s-fusion').checked)   strategies.push('Fusion(no-ML)');
      if ($('#f-s-fusionml').checked) strategies.push('Fusion(+ML-veto)');

      if (strategies.length === 0) {
        showToast('⚠️ 请至少选择一个策略', 'error');
        return;
      }

      // ✅ 修复：严格验证日期格式
      const startDateStr = ($('#f-start-date').value || '').trim();
      const endDateStr = ($('#f-end-date').value || '').trim();
      
      if (!startDateStr || !endDateStr) {
        showToast('⚠️ 请填写开始和结束日期', 'error');
        return;
      }

      // 将 YYYY-MM-DD 转换为 YYYYMMDD，去除所有非数字字符
      const rawStart = startDateStr.replace(/\D/g, '');
      const rawEnd = endDateStr.replace(/\D/g, '');
      
      // ✅ 验证格式（必须是 8 位数字）
      if (rawStart.length !== 8 || rawEnd.length !== 8) {
        showToast(
          `⚠️ 日期格式错误。开始日期: ${rawStart.length !== 8 ? '❌ ' + rawStart : '✓'}, ` +
          `结束日期: ${rawEnd.length !== 8 ? '❌ ' + rawEnd : '✓'}`,
          'error'
        );
        return;
      }
      
      // ✅ 验证日期逻辑
      if (rawStart >= rawEnd) {
        showToast('⚠️ 开始日期必须早于结束日期', 'error');
        return;
      }
      
      // ✅ 验证年份（允许 1990-2100）
      const startYear = parseInt(rawStart.substring(0, 4), 10);
      const endYear = parseInt(rawEnd.substring(0, 4), 10);
      if (startYear < 1990 || startYear > 2100 || endYear < 1990 || endYear > 2100) {
        showToast(
          `⚠️ 年份必须在 1990-2100 年之间 (开始: ${startYear}, 结束: ${endYear})`,
          'error'
        );
        return;
      }
      
      // ✅ 验证月份和日期（真实有效性检查，可检测如 2024-02-31 等非法日期）
      const startMonth = parseInt(rawStart.substring(4, 6), 10);
      const startDay   = parseInt(rawStart.substring(6, 8), 10);
      const endMonth   = parseInt(rawEnd.substring(4, 6), 10);
      const endDay     = parseInt(rawEnd.substring(6, 8), 10);

      if (!isValidDate(startYear, startMonth, startDay)) {
        showToast(
          `⚠️ 开始日期不合法：${rawStart.substring(0,4)}-${rawStart.substring(4,6)}-${rawStart.substring(6,8)}`,
          'error'
        );
        return;
      }
      if (!isValidDate(endYear, endMonth, endDay)) {
        showToast(
          `⚠️ 结束日期不合法：${rawEnd.substring(0,4)}-${rawEnd.substring(4,6)}-${rawEnd.substring(6,8)}`,
          'error'
        );
        return;
      }

      const numVal = (id, def) => { const v = parseFloat($('#' + id).value); return isNaN(v) ? def : v; };
      const intVal = (id, def) => { const v = parseInt($('#' + id).value);   return isNaN(v) ? def : v; };

      const params = {
        symbol:         ($('#f-symbol').value || '').trim() || '600519',
        start_date:     rawStart,
        end_date:       rawEnd,
        init_cash:      numVal('f-init-cash',  100000),
        fee_rate:       numVal('f-fee-rate',   0.3) / 1000,
        slippage_bps:   numVal('f-slippage',   5),
        trend_short:    intVal('f-trend-short', 5),
        trend_long:     intVal('f-trend-long',  20),
        mr_window:      intVal('f-mr-window',   20),
        mr_num_std:     numVal('f-mr-std',      1.2),
        ml_lookback:    intVal('f-ml-lookback', 10),
        ml_min_train:   intVal('f-ml-train',    60),
        strategies,
      };

      const btn          = $('#run-backtest-btn');
      const statusEl     = $('#backtest-status');
      const progressWrap = $('#backtest-progress');
      const progressBar  = $('#backtest-progress-bar');
      const progressText = $('#backtest-progress-text');

      btn.disabled    = true;
      btn.textContent = '⏳ 运行中…';
      if (statusEl)     statusEl.textContent = '正在启动回测…';
      if (progressWrap) progressWrap.classList.remove('hidden');
      if (progressBar)  progressBar.style.width = '0%';
      if (progressText) progressText.textContent = '等待开始…';

      if (state.backtestPollTimer) { clearTimeout(state.backtestPollTimer); state.backtestPollTimer = null; }

      try {
        // 1. POST to start background task; returns immediately with task_id
        const startRes  = await fetch(CONFIG.apiBase + '/api/backtest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        });
        const startJson = await startRes.json();
        if (!startRes.ok || startJson.error) throw new Error(startJson.error || `HTTP ${startRes.status}`);

        const taskId = startJson.task_id;

        // 2. Poll status until done or error
        await new Promise((resolve, reject) => {
          async function poll() {
            try {
              const r = await fetch(`${CONFIG.apiBase}/api/backtest-status/${taskId}`);
              const j = await r.json();
              const pct = j.progress || 0;
              if (progressBar)  progressBar.style.width = pct + '%';
              if (progressText) progressText.textContent = j.message || '运行中…';
              if (statusEl)     statusEl.textContent = `${pct}% — ${j.message || ''}`;

              if (j.status === 'done') {
                state.data = j.result;
                resolve();
              } else if (j.status === 'error') {
                reject(new Error(j.error || '回测失败'));
              } else {
                state.backtestPollTimer = setTimeout(poll, 2000);
              }
            } catch (err) { reject(err); }
          }
          poll();
        });

        // 3. Render results
        Object.values(state.charts).forEach(c => { try { if (c && c.destroy) c.destroy(); } catch (_) {} });
        state.charts = {};
        render(state.data);
        if (statusEl) statusEl.textContent = '✅ 回测完成！';
        showToast('✅ 回测完成，数据已更新', 'success');
        switchPage('dashboard');

      } catch (err) {
        console.error(err);
        if (statusEl) statusEl.textContent = '❌ ' + err.message;
        showToast('❌ 回测失败：' + err.message, 'error');
      } finally {
        btn.disabled    = false;
        btn.textContent = '🚀 运行回测';
        if (progressWrap) progressWrap.classList.add('hidden');
      }
    });
  }

  // ── Sidebar / Mobile ─────────────────────────────────────────────────
  function initSidebar() {
    const hamburger = $('#hamburger-btn');
    const sidebar   = $('.sidebar');
    const backdrop  = $('#sidebar-backdrop');
    if (!hamburger || !sidebar || !backdrop) return;

    function open()  { sidebar.classList.add('open');    backdrop.classList.add('visible'); }
    function close() { sidebar.classList.remove('open'); backdrop.classList.remove('visible'); }

    hamburger.addEventListener('click', open);
    backdrop.addEventListener('click', close);

    $$('.nav-item').forEach(item => {
      item.addEventListener('click', () => {
        const page = item.dataset.page;
        if (page) switchPage(page);
        if (window.innerWidth < 768) close();
      });
    });
  }

  // ── Init ─────────────────────────────────────────────────────────────
  async function init() {
    initSidebar();
    initTableSort();
    initBacktestForm();
    initDatePresets();

    // Export CSV button
    const exportBtn = $('#export-csv-btn');
    if (exportBtn) exportBtn.addEventListener('click', exportTradesToCSV);

    const apiUrlEl = $('#api-base-url');
    if (apiUrlEl) apiUrlEl.textContent = CONFIG.apiBase;

    const sel = $('#strategy-select');
    if (sel) {
      sel.addEventListener('change', () => {
        state.activeStrategy = sel.value;
        state.currentPage = 1;
        if (state.data) render(state.data);
      });
    }

    const searchInput = $('#trade-search');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        state.searchQuery = searchInput.value.trim();
        state.currentPage = 1;
        if (state.data) renderTradesTable(state.data);
      });
    }

    const refreshBtn = $('#refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => refresh(false));
    }

    await detectApi();

    const data = await loadData();
    state.data = data;

    const overlay = $('#loading-overlay');
    if (overlay) overlay.classList.add('hidden');
    setTimeout(() => { if (overlay) overlay.style.display = 'none'; }, 500);

    if (state.data) {
      render(state.data);
    } else {
      showToast('无��加载数据文件', 'error');
    }

    state.refreshTimer = setInterval(() => refresh(true), CONFIG.refreshInterval);
  }

  document.addEventListener('DOMContentLoaded', init);
})();