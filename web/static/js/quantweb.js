/* QuantWeb — 前端交互 JS
   ============================================
   - SSE 任务进度推送
   - 数字格式化（千分位 + 正负号）
   - 锚点平滑滚动
   - 图表按需加载
*/

(function () {
    'use strict';

    // ── 数字格式化 ──
    // 所有 class="num" 的 DOM 元素自动格式化
    function formatNumber(el) {
        const raw = el.getAttribute('data-raw') || el.textContent.trim();
        const num = parseFloat(raw);
        if (isNaN(num)) return;
        const abs = Math.abs(num);
        const sign = num >= 0 ? '+' : '-';
        const parts = abs.toFixed(2).split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        el.textContent = sign + parts.join('.');
        el.classList.add('num-mono');
        el.classList.toggle('text-up', num > 0);
        el.classList.toggle('text-down', num < 0);
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.num').forEach(formatNumber);
    });

    // ── 锚点平滑滚动 ──
    document.addEventListener('click', function (e) {
        const link = e.target.closest('a[href^="#"]');
        if (!link) return;
        e.preventDefault();
        const target = document.querySelector(link.getAttribute('href'));
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });

    // ── SSE 任务进度 ──
    const progressEl = document.getElementById('task-progress');
    if (progressEl) {
        const runId = progressEl.getAttribute('data-run-id');
        if (runId) {
            const evtSource = new EventSource('/api/runs/' + runId + '/stream');
            evtSource.addEventListener('progress', function (e) {
                const data = JSON.parse(e.data);
                const bar = document.getElementById('progress-bar');
                const text = document.getElementById('progress-text');
                if (bar) {
                    const pct = data.total > 0 ? (data.done / data.total) * 100 : 0;
                    bar.style.width = pct + '%';
                    bar.setAttribute('aria-valuenow', pct);
                }
                if (text) {
                    text.textContent = data.done + '/' + data.total;
                }
                if (data.status !== 'running') {
                    evtSource.close();
                    if (data.status === 'completed') {
                        window.location.reload();
                    }
                }
            });
            evtSource.addEventListener('error', function () {
                // SSE 连接断开，fallback 到页面刷新
                evtSource.close();
            });
        }
    }

    // ── 发起回测表单提交 ──
    const backtestForm = document.getElementById('backtest-form');
    if (backtestForm) {
        backtestForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(backtestForm);
            fetch('/api/backtest/run', {
                method: 'POST',
                body: formData,
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.run_id) {
                        window.location.href = '/runs/' + data.run_id;
                    } else {
                        alert('发起回测失败: ' + (data.error || '未知错误'));
                    }
                })
                .catch(function (err) {
                    alert('网络错误: ' + err.message);
                });
        });
    }

    // ── K 线图按需加载 ──
    const klineBtn = document.getElementById('kline-load');
    if (klineBtn) {
        klineBtn.addEventListener('click', function () {
            const code = document.getElementById('kline-code').value;
            const runId = klineBtn.getAttribute('data-run-id');
            const container = document.getElementById('kline-chart');
            if (!code || !runId || !container) return;
            container.innerHTML = '<div class="text-center py-4"><div class="spinner-border text-secondary" role="status"></div><p class="mt-2 text-secondary">正在加载 K 线数据...</p></div>';
            fetch('/api/runs/' + runId + '/kline/' + code)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.html) {
                        container.innerHTML = data.html;
                    } else {
                        container.innerHTML = '<p class="text-danger">K 线数据加载失败</p>';
                    }
                })
                .catch(function () {
                    container.innerHTML = '<p class="text-danger">K 线数据网络请求失败</p>';
                });
        });
    }

})();