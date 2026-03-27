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
  };

  // ── DOM helpers ──────────────────────────────────────────────────────────
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

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
      if (pageId === 'analysis') renderAnalysisCharts(state.data);
      if (pageId === 'strategy') renderStrategyCards(state.data);
      if (pageId === 'trades')   renderTradesTable(state.data);
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
      state.charts.equity.update('none');
      return;
    }
    // Destroy any stale Chart.js instance registered on this canvas
    const existing = Chart.getChart(ctx);
    if (existing) existing.destroy();
    state.charts.equity = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets: ds },
      options: equityChartOptions(),
    });
  }

  function buildEquityDatasets(data) {
    const ec = data.equityCurves;
    return [
      { key: 'Trend-only',       label: 'Trend-only',       color: COLORS.trend },
      { key: 'MeanRev-only',     label: 'MeanRev-only',     color: COLORS.meanrev },
      { key: 'Fusion(no-ML)',    label: 'Fusion(no-ML)',    color: COLORS.fusion },
      { key: 'Fusion(+ML-veto)', label: 'Fusion(+ML-veto)', color: COLORS.fusionML },
    ].filter(d => ec[d.key] && ec[d.key].length).map(d => ({
      label: d.label,
      data: (ec[d.key] || []).map(v => +v.toFixed(2)),
      borderColor: d.color,
      backgroundColor: d.color + '18',
      borderWidth: 2.5,
      pointRadius: 0,
      pointHoverRadius: 5,
      tension: 0.2,
      fill: false,
    }));
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
          type: 'category',
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
      state.charts.equityFull.update('none');
      return;
    }
    // Destroy any stale Chart.js instance registered on this canvas
    const existing = Chart.getChart(ctx);
    if (existing) existing.destroy();
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
      state.charts.drawdown.update('none');
      return;
    }
    // Destroy any stale Chart.js instance registered on this canvas
    const existingDd = Chart.getChart(ctx);
    if (existingDd) existingDd.destroy();
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
          x: { type: 'category', grid: { color: '#1e293b' }, ticks: { color: '#64748b', maxTicksLimit: 8 } },
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
      state.charts.winloss.update('none');
      return;
    }
    // Destroy any stale Chart.js instance registered on this canvas
    const existingWl = Chart.getChart(ctx);
    if (existingWl) existingWl.destroy();
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
    if (state.activePage === 'analysis')  renderAnalysisCharts(data);
    if (state.activePage === 'strategy')  renderStrategyCards(data);
    if (state.activePage === 'trades')    renderTradesTable(data);
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
      
      // ✅ 验证年份（允许 1990 至今）
      const today = new Date();
      const todayYear = today.getFullYear();
      const todayStr = todayYear.toString() +
        String(today.getMonth() + 1).padStart(2, '0') +
        String(today.getDate()).padStart(2, '0');
      const todayDisplay = `${todayYear}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;

      const startYear = parseInt(rawStart.substring(0, 4), 10);
      const endYear = parseInt(rawEnd.substring(0, 4), 10);
      if (startYear < 1990 || startYear > todayYear || endYear < 1990 || endYear > todayYear) {
        showToast(
          `⚠️ 年份必须在 1990-${todayYear} 之间 (开始: ${startYear}, 结束: ${endYear})`,
          'error'
        );
        return;
      }

      // ✅ 验证结束日期不能超过今天（无未来市场数据）
      if (rawEnd > todayStr) {
        showToast(
          `⚠️ 结束日期不能超过今天 (${todayDisplay})，市场数据尚未产生`,
          'error'
        );
        return;
      }
      
      // ✅ 验证月份和日期
      const startMonth = parseInt(rawStart.substring(4, 6), 10);
      const startDay = parseInt(rawStart.substring(6, 8), 10);
      const endMonth = parseInt(rawEnd.substring(4, 6), 10);
      const endDay = parseInt(rawEnd.substring(6, 8), 10);
      
      if (startMonth < 1 || startMonth > 12 || startDay < 1 || startDay > 31) {
        showToast(
          `⚠️ 开始日期不合法 (月: ${startMonth}, 日: ${startDay})`,
          'error'
        );
        return;
      }
      if (endMonth < 1 || endMonth > 12 || endDay < 1 || endDay > 31) {
        showToast(
          `⚠️ 结束日期不合法 (月: ${endMonth}, 日: ${endDay})`,
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

      const btn    = $('#run-backtest-btn');
      const status = $('#backtest-status');
      btn.disabled = true;
      btn.textContent = '⏳ 运行中…';
      if (status) status.textContent = '正在运行回测，可能需要 10~60 秒…';

      try {
        const res = await fetch(CONFIG.apiBase + '/api/backtest', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(params),
        });
        const json = await res.json();
        if (!res.ok || json.error) throw new Error(json.error || `HTTP ${res.status}`);

        state.data = json;
        Object.values(state.charts).forEach(c => {
          try { if (c && c.destroy) c.destroy(); } catch (_) { }
        });
        state.charts = {};

        render(state.data);
        if (status) status.textContent = '✅ 回测完成！';
        showToast('✅ 回测完成，数据已更新', 'success');
        switchPage('dashboard');

      } catch (err) {
        console.error(err);
        if (status) status.textContent = '❌ ' + err.message;
        showToast('❌ 回测失败：' + err.message, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = '🚀 运行回测';
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

    // Restrict date pickers to today at the latest (no future market data)
    const todayIso = new Date().toISOString().slice(0, 10);
    const startEl = $('#f-start-date');
    const endEl   = $('#f-end-date');
    if (startEl) startEl.setAttribute('max', todayIso);
    if (endEl)   { endEl.setAttribute('max', todayIso); if (endEl.value > todayIso) endEl.value = todayIso; }

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