/* MindScope GEO — Dashboard JavaScript */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentBrandId = null;
let charts = {};
let refreshInterval = null;

const ENGINE_COLORS = {
    chatgpt:    { bg: 'rgba(16,163,127,0.8)',  border: '#10A37F' },
    gemini:     { bg: 'rgba(66,133,244,0.8)',  border: '#4285F4' },
    perplexity: { bg: 'rgba(0,199,183,0.8)',   border: '#00C7B7' },
    claude:     { bg: 'rgba(204,153,0,0.8)',   border: '#CC9900' },
};

const ENGINE_LABELS = {
    chatgpt: 'ChatGPT',
    gemini: 'Gemini',
    perplexity: 'Perplexity',
    claude: 'Claude',
};

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    loadBrands();
    setupEventListeners();
    startAutoRefresh();
});

function setupEventListeners() {
    document.getElementById('brandSelect').addEventListener('change', (e) => {
        const id = e.target.value;
        if (id) {
            currentBrandId = parseInt(id);
            loadDashboardData();
        } else {
            currentBrandId = null;
            showNoBrandState();
        }
    });

    document.getElementById('btnScan').addEventListener('click', runScan);
    document.getElementById('btnPrompts').addEventListener('click', openPromptModal);
    document.getElementById('btnReport').addEventListener('click', downloadReport);

    // Modal
    document.getElementById('modalBackdrop').addEventListener('click', closePromptModal);
    document.getElementById('modalClose').addEventListener('click', closePromptModal);
    document.getElementById('btnAddPrompt').addEventListener('click', addPrompt);
    document.getElementById('btnGeneratePrompts').addEventListener('click', generatePrompts);
}

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function getToken() {
    return localStorage.getItem('geo_token') || '';
}

function logout() {
    localStorage.removeItem('geo_token');
    localStorage.removeItem('geo_user');
    window.location.href = '/login';
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function api(url, options = {}) {
    try {
        const token = getToken();
        const headers = { 'Content-Type': 'application/json' };
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        const resp = await fetch(url, {
            headers,
            ...options,
        });

        // 401이면 로그인 페이지로
        if (resp.status === 401) {
            logout();
            return;
        }

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        return await resp.json();
    } catch (e) {
        showToast(e.message, 'error');
        throw e;
    }
}

// ---------------------------------------------------------------------------
// Brand loading
// ---------------------------------------------------------------------------
async function loadBrands() {
    try {
        const clients = await api('/api/clients');
        const select = document.getElementById('brandSelect');
        select.innerHTML = '<option value="">브랜드를 선택하세요</option>';

        clients.forEach(client => {
            if (client.brands && client.brands.length > 0) {
                const group = document.createElement('optgroup');
                group.label = client.name;
                client.brands.forEach(brand => {
                    const opt = document.createElement('option');
                    opt.value = brand.id;
                    opt.textContent = brand.name;
                    group.appendChild(opt);
                });
                select.appendChild(group);
            }
        });

        // 브랜드가 없으면 설정 페이지 안내
        const totalBrands = clients.reduce((sum, c) => sum + (c.brands?.length || 0), 0);
        if (totalBrands === 0) {
            showNoBrandState(true);
        }
    } catch (e) {
        console.error('브랜드 목록 로드 실패:', e);
    }
}

// ---------------------------------------------------------------------------
// Dashboard data loading
// ---------------------------------------------------------------------------
async function loadDashboardData() {
    if (!currentBrandId) return;

    document.getElementById('dashboardContent').style.display = '';
    document.getElementById('noBrandState').style.display = 'none';

    try {
        const [latestScores, history, results, competitors] = await Promise.all([
            api(`/api/scores/${currentBrandId}/latest`),
            api(`/api/scores/${currentBrandId}`),
            api(`/api/results/${currentBrandId}`),
            api(`/api/competitors/${currentBrandId}`),
        ]);

        updateMetricCards(latestScores);
        updateVisibilityChart(history);
        updateSovChart(competitors);
        updateEngineBarChart(latestScores);
        updateResultsTable(results);
        updateStatusBar(latestScores, results);

        // Load optimization recommendations
        loadRecommendations();
    } catch (e) {
        console.error('대시보드 데이터 로드 실패:', e);
    }
}

// ---------------------------------------------------------------------------
// Metric Cards
// ---------------------------------------------------------------------------
function updateMetricCards(data) {
    const s = data.summary || {};
    setMetric('metricVisibility', formatNumber(s.avg_visibility || 0, 1) + '%', '평균 가시성');
    setMetric('metricSov', formatNumber(s.avg_sov || 0, 1) + '%', 'AI 점유율');

    const sentVal = s.avg_sentiment || 0;
    const sentStr = (sentVal >= 0 ? '+' : '') + formatNumber(sentVal, 2);
    setMetric('metricSentiment', sentStr, '평균 감성', sentVal < 0 ? 'negative' : '');

    const promptCount = s.total_prompts_tracked || 0;
    setMetric('metricPrompts', formatNumber(promptCount, 0), '추적 프롬프트');
}

function setMetric(id, value, label, extraClass = '') {
    const card = document.getElementById(id);
    if (!card) return;
    card.querySelector('.metric-value').textContent = value;
    if (extraClass) {
        card.classList.add(extraClass);
    } else {
        card.classList.remove('negative');
    }
}

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------
function getChartDefaults() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: { color: '#A0AEC0', font: { size: 12 }, padding: 16 },
            },
            tooltip: {
                backgroundColor: '#2D3748',
                titleColor: '#FAFBFC',
                bodyColor: '#A0AEC0',
                borderColor: 'rgba(255,255,255,0.08)',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
            },
        },
    };
}

function updateVisibilityChart(data) {
    const ctx = document.getElementById('visibilityChart');
    if (!ctx) return;

    const history = data.history || {};
    const datasets = [];

    Object.entries(history).forEach(([engine, points]) => {
        const colors = ENGINE_COLORS[engine] || { bg: 'rgba(255,255,255,0.5)', border: '#fff' };
        datasets.push({
            label: ENGINE_LABELS[engine] || engine,
            data: points.map(p => ({ x: p.date, y: p.score })),
            borderColor: colors.border,
            backgroundColor: colors.bg.replace('0.8', '0.1'),
            borderWidth: 2,
            pointRadius: 4,
            pointHoverRadius: 6,
            tension: 0.3,
            fill: true,
        });
    });

    if (charts.visibility) {
        charts.visibility.data.datasets = datasets;
        charts.visibility.update();
    } else {
        charts.visibility = new Chart(ctx, {
            type: 'line',
            data: { datasets },
            options: {
                ...getChartDefaults(),
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: { color: '#A0AEC0', font: { size: 11 } },
                    },
                    y: {
                        min: 0,
                        max: 100,
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: {
                            color: '#A0AEC0',
                            font: { size: 11 },
                            callback: v => v + '%',
                        },
                    },
                },
            },
        });
    }
}

function updateSovChart(data) {
    const ctx = document.getElementById('sovChart');
    if (!ctx) return;

    const sov = data.share_of_voice || {};
    const labels = Object.keys(sov);
    const values = Object.values(sov);
    const brandName = data.brand_name || '';

    const colors = labels.map(name =>
        name === brandName ? '#C6FF3D' : getCompetitorColor(labels.indexOf(name))
    );

    if (charts.sov) {
        charts.sov.data.labels = labels;
        charts.sov.data.datasets[0].data = values;
        charts.sov.data.datasets[0].backgroundColor = colors;
        charts.sov.update();
    } else {
        charts.sov = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderColor: '#0B0F19',
                    borderWidth: 3,
                    hoverOffset: 6,
                }],
            },
            options: {
                ...getChartDefaults(),
                cutout: '65%',
                plugins: {
                    ...getChartDefaults().plugins,
                    legend: {
                        position: 'bottom',
                        labels: { color: '#A0AEC0', font: { size: 11 }, padding: 12 },
                    },
                },
            },
        });
    }
}

function updateEngineBarChart(data) {
    const ctx = document.getElementById('engineBarChart');
    if (!ctx) return;

    const engines = data.engines || {};
    const labels = [];
    const values = [];
    const bgColors = [];
    const borderColors = [];

    Object.entries(engines).forEach(([engine, info]) => {
        labels.push(ENGINE_LABELS[engine] || engine);
        values.push(info.score || 0);
        const c = ENGINE_COLORS[engine] || { bg: 'rgba(255,255,255,0.5)', border: '#fff' };
        bgColors.push(c.bg);
        borderColors.push(c.border);
    });

    if (charts.engineBar) {
        charts.engineBar.data.labels = labels;
        charts.engineBar.data.datasets[0].data = values;
        charts.engineBar.data.datasets[0].backgroundColor = bgColors;
        charts.engineBar.data.datasets[0].borderColor = borderColors;
        charts.engineBar.update();
    } else {
        charts.engineBar = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: 'Visibility Score',
                    data: values,
                    backgroundColor: bgColors,
                    borderColor: borderColors,
                    borderWidth: 1,
                    borderRadius: 6,
                    barPercentage: 0.6,
                }],
            },
            options: {
                ...getChartDefaults(),
                indexAxis: 'y',
                plugins: {
                    ...getChartDefaults().plugins,
                    legend: { display: false },
                },
                scales: {
                    x: {
                        min: 0,
                        max: 100,
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: {
                            color: '#A0AEC0',
                            font: { size: 11 },
                            callback: v => v + '%',
                        },
                    },
                    y: {
                        grid: { display: false },
                        ticks: { color: '#A0AEC0', font: { size: 12 } },
                    },
                },
            },
        });
    }
}

// ---------------------------------------------------------------------------
// Results Table
// ---------------------------------------------------------------------------
function updateResultsTable(results) {
    const tbody = document.getElementById('resultsBody');
    if (!tbody) return;

    if (!results || results.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align:center; color:var(--fog); padding:40px;">
                    스캔 결과가 없습니다. 스캔을 실행해주세요.
                </td>
            </tr>`;
        return;
    }

    tbody.innerHTML = results.slice(0, 30).map(r => {
        const mentionIcon = r.brand_mentioned
            ? '<span class="mention-yes">&#10003;</span>'
            : '<span class="mention-no">&#10007;</span>';

        const sentVal = r.sentiment_score || 0;
        const sentClass = sentVal >= 0 ? 'positive' : 'negative';
        const sentWidth = Math.abs(sentVal) * 50;

        const engineClass = r.engine || '';
        const engineLabel = ENGINE_LABELS[r.engine] || r.engine;

        const time = r.scanned_at ? new Date(r.scanned_at).toLocaleString('ko-KR', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
        }) : '';

        return `<tr>
            <td style="max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                title="${escapeHtml(r.prompt_text)}">${escapeHtml(r.prompt_text)}</td>
            <td><span class="engine-badge ${engineClass}">${engineLabel}</span></td>
            <td>${mentionIcon}</td>
            <td>
                <div class="sentiment-bar">
                    <div class="sentiment-bar-track">
                        <div class="sentiment-bar-fill ${sentClass}" style="width:${sentWidth}%"></div>
                    </div>
                    <span class="sentiment-value">${sentVal >= 0 ? '+' : ''}${sentVal.toFixed(2)}</span>
                </div>
            </td>
            <td style="white-space:nowrap; color:var(--fog); font-size:0.78rem;">${time}</td>
        </tr>`;
    }).join('');
}

// ---------------------------------------------------------------------------
// Status Bar
// ---------------------------------------------------------------------------
function updateStatusBar(latestScores, results) {
    const el = document.getElementById('statusText');
    if (!el) return;
    const engineCount = Object.keys(latestScores.engines || {}).length;
    const resultCount = results?.length || 0;
    el.textContent = `${engineCount}개 엔진 모니터링 중 · 최근 결과 ${resultCount}건`;
}

// ---------------------------------------------------------------------------
// Scan
// ---------------------------------------------------------------------------
async function runScan() {
    if (!currentBrandId) {
        showToast('먼저 브랜드를 선택하세요.', 'error');
        return;
    }

    const btn = document.getElementById('btnScan');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner dark"></span> 스캔 중...';

    showLoadingOverlay('AI 엔진에 프롬프트를 전송하고 있습니다...');

    try {
        const result = await api(`/api/scan/${currentBrandId}`, { method: 'POST' });
        showToast(`스캔 완료: ${result.total_queries}건 쿼리 실행`, 'success');
        await loadDashboardData();
    } catch (e) {
        // error already shown via api()
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
        hideLoadingOverlay();
    }
}

// ---------------------------------------------------------------------------
// Prompt Modal
// ---------------------------------------------------------------------------
async function openPromptModal() {
    if (!currentBrandId) {
        showToast('먼저 브랜드를 선택하세요.', 'error');
        return;
    }

    document.getElementById('modalBackdrop').classList.add('active');
    document.getElementById('promptModal').classList.add('active');

    // Load prompts
    try {
        const brand = await api(`/api/brands/${currentBrandId}`);
        const promptList = document.getElementById('promptList');

        // Get prompts from results (we don't have a direct prompts list endpoint, use brand info)
        const results = await api(`/api/results/${currentBrandId}?limit=200`);
        const uniquePrompts = new Map();
        results.forEach(r => {
            if (!uniquePrompts.has(r.prompt_text)) {
                uniquePrompts.set(r.prompt_text, r);
            }
        });

        if (uniquePrompts.size === 0) {
            promptList.innerHTML = '<li class="prompt-item" style="color:var(--fog); justify-content:center;">등록된 프롬프트가 없습니다.</li>';
        } else {
            promptList.innerHTML = Array.from(uniquePrompts.entries()).map(([text]) =>
                `<li class="prompt-item">
                    <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(text)}</span>
                </li>`
            ).join('');
        }
    } catch (e) {
        console.error('프롬프트 로드 실패:', e);
    }
}

function closePromptModal() {
    document.getElementById('modalBackdrop').classList.remove('active');
    document.getElementById('promptModal').classList.remove('active');
}

async function addPrompt() {
    if (!currentBrandId) return;

    const input = document.getElementById('newPromptText');
    const text = input.value.trim();
    if (!text) {
        showToast('프롬프트 텍스트를 입력하세요.', 'error');
        return;
    }

    try {
        await api(`/api/brands/${currentBrandId}/prompts`, {
            method: 'POST',
            body: JSON.stringify({ prompt_text: text, category: '수동' }),
        });
        showToast('프롬프트가 추가되었습니다.', 'success');
        input.value = '';
        openPromptModal(); // refresh list
    } catch (e) {
        // error shown via api()
    }
}

async function generatePrompts() {
    if (!currentBrandId) return;

    const btn = document.getElementById('btnGeneratePrompts');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner dark"></span> 생성 중...';

    try {
        const result = await api(`/api/brands/${currentBrandId}/prompts/generate`, { method: 'POST' });
        showToast(`${result.generated_count}개 프롬프트가 자동 생성되었습니다.`, 'success');
        openPromptModal(); // refresh list
    } catch (e) {
        // error shown via api()
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------
function downloadReport() {
    if (!currentBrandId) {
        showToast('먼저 브랜드를 선택하세요.', 'error');
        return;
    }
    window.open(`/report/${currentBrandId}`, '_blank');
}

// ---------------------------------------------------------------------------
// No brand state
// ---------------------------------------------------------------------------
function showNoBrandState(noData = false) {
    document.getElementById('dashboardContent').style.display = 'none';
    const el = document.getElementById('noBrandState');
    el.style.display = '';
    if (noData) {
        el.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">&#x1F50D;</div>
                <h3>등록된 브랜드가 없습니다</h3>
                <p>초기 설정에서 클라이언트와 브랜드를 먼저 등록하세요.</p>
                <a href="/setup" class="btn btn-primary">초기 설정하기</a>
            </div>`;
    } else {
        el.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">&#x2191;</div>
                <h3>브랜드를 선택하세요</h3>
                <p>상단 드롭다운에서 모니터링할 브랜드를 선택하면 대시보드가 표시됩니다.</p>
            </div>`;
    }
}

// ---------------------------------------------------------------------------
// Auto refresh
// ---------------------------------------------------------------------------
function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        if (currentBrandId) {
            loadDashboardData();
        }
    }, 5 * 60 * 1000); // 5분
}

// ---------------------------------------------------------------------------
// UI utilities
// ---------------------------------------------------------------------------
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function showLoadingOverlay(text = '로딩 중...') {
    const overlay = document.getElementById('loadingOverlay');
    if (!overlay) return;
    overlay.querySelector('p').textContent = text;
    overlay.classList.add('active');
}

function hideLoadingOverlay() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.remove('active');
}

function formatNumber(num, decimals = 0) {
    return Number(num).toLocaleString('ko-KR', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function getCompetitorColor(index) {
    const colors = [
        '#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A',
        '#98D8C8', '#F7DC6F', '#BB8FCE', '#85C1E9',
    ];
    return colors[index % colors.length];
}

// ---------------------------------------------------------------------------
// Optimization Recommendations
// ---------------------------------------------------------------------------
async function loadRecommendations() {
    if (!currentBrandId) return;

    const container = document.getElementById('recommendationsContainer');
    if (!container) return;

    try {
        const data = await api(`/api/recommendations/${currentBrandId}`);
        renderRecommendations(data.recommendations || [], container);
    } catch (e) {
        container.innerHTML = '<p style="text-align:center; color:var(--fog); padding:20px;">추천 데이터를 불러올 수 없습니다.</p>';
    }
}

function renderRecommendations(recommendations, container) {
    if (!recommendations || recommendations.length === 0) {
        container.innerHTML = '<p style="text-align:center; color:var(--fog); padding:20px;">현재 추천 사항 없음 (모든 점수가 양호합니다)</p>';
        return;
    }

    const priorityStyles = {
        high:   { color: '#FF2D5F', bg: 'rgba(255,45,95,0.12)', label: 'HIGH' },
        medium: { color: '#C6FF3D', bg: 'rgba(198,255,61,0.12)', label: 'MEDIUM' },
        low:    { color: '#A0AEC0', bg: 'rgba(160,174,192,0.08)', label: 'LOW' },
    };

    const categoryLabels = {
        technical: '기술',
        content: '콘텐츠',
        authority: '권위',
        monitoring: '모니터링',
    };

    container.innerHTML = recommendations.map(rec => {
        const ps = priorityStyles[rec.priority] || priorityStyles.low;
        const catLabel = categoryLabels[rec.category] || rec.category;

        return `
        <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; margin-bottom:10px;">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                <span style="background:${ps.bg}; color:${ps.color}; padding:2px 10px; border-radius:10px; font-size:0.7rem; font-weight:700; letter-spacing:0.04em;">${ps.label}</span>
                <span style="background:rgba(255,255,255,0.05); color:var(--fog); padding:2px 10px; border-radius:10px; font-size:0.7rem;">${escapeHtml(catLabel)}</span>
                ${rec.effort ? `<span style="margin-left:auto; color:var(--fog); font-size:0.7rem;">난이도: ${escapeHtml(rec.effort)}</span>` : ''}
            </div>
            <h4 style="color:var(--text-primary, #FAFBFC); font-size:0.9rem; margin:0 0 6px 0; font-weight:600;">${escapeHtml(rec.title)}</h4>
            <p style="color:var(--fog, #A0AEC0); font-size:0.82rem; line-height:1.6; margin:0 0 6px 0;">${escapeHtml(rec.description)}</p>
            <p style="color:var(--fog, #718096); font-size:0.76rem; margin:0;"><strong style="color:var(--fog, #A0AEC0);">예상 효과:</strong> ${escapeHtml(rec.expected_impact)}</p>
        </div>`;
    }).join('');
}
