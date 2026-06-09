// Server Health Page — polls /api/health/detail every 10s

let healthInterval = null;

function formatUptime(hours) {
    if (hours < 1) return `${Math.round(hours * 60)}m`;
    if (hours < 24) return `${hours.toFixed(1)}h`;
    const d = Math.floor(hours / 24);
    const h = Math.round(hours % 24);
    return `${d}d ${h}h`;
}

function chip(ok, label) {
    const cls = ok ? 'health-chip--ok' : 'health-chip--err';
    const icon = ok ? '✓' : '✗';
    return `<span class="health-chip ${cls}">${icon} ${label}</span>`;
}

function resourceBar(label, pct) {
    const cls = pct > 85 ? 'resource-bar-fill--err' : pct > 65 ? 'resource-bar-fill--warn' : 'resource-bar-fill--ok';
    return `
        <div class="resource-bar-wrap">
            <div class="resource-label"><span>${label}</span><span>${pct.toFixed(1)}%</span></div>
            <div class="resource-bar"><div class="resource-bar-fill ${cls}" style="width:${pct}%"></div></div>
        </div>`;
}

function renderHealthPage(data) {
    const banner = document.getElementById('healthBanner');
    const dot = document.getElementById('healthDot');
    const bannerText = document.getElementById('healthBannerText');
    const bannerMeta = document.getElementById('healthBannerMeta');
    const cards = document.getElementById('healthCards');

    const isOnline = data.status === 'online';

    // Banner
    banner.className = `health-banner ${isOnline ? 'health-banner--ok' : 'health-banner--err'}`;
    dot.className = `health-dot ${isOnline ? 'health-dot--ok' : 'health-dot--err'}`;
    bannerText.textContent = isOnline ? 'All Systems Online' : 'Server Issue Detected';
    bannerMeta.textContent = `Uptime: ${formatUptime(data.uptime_hours)} • Last check: ${new Date(data.timestamp).toLocaleTimeString()}`;

    const c = data.components || {};
    const trading = c.trading_engine || {};
    const scan = c.scan_loop || {};
    const sys = data.system || {};

    let html = '';

    // ── Components Card ──
    html += `<div class="health-card">
        <div class="health-card-title">Components</div>
        <div class="health-chips">
            ${chip(c.analyzer, 'Analyzer')}
            ${chip(c.ml_pipeline, 'ML Pipeline')}
            ${chip(c.adk_orchestrator, 'ADK Orchestrator')}
        </div>
    </div>`;

    // ── Trading Engine Card ──
    html += `<div class="health-card">
        <div class="health-card-title">Trading Engine</div>`;
    if (trading.error) {
        html += `<div class="health-metric"><span class="health-metric-label">Status</span><span class="health-metric-value" style="color:var(--error)">Unavailable</span></div>`;
    } else {
        html += `
            <div class="health-metric"><span class="health-metric-label">Active</span><span class="health-metric-value">${trading.active ? 'Yes' : 'No'}</span></div>
            <div class="health-metric"><span class="health-metric-label">Kill Switch</span><span class="health-metric-value" style="color:${trading.kill_switch ? 'var(--error)' : 'var(--success)'}">${trading.kill_switch ? 'ON' : 'Off'}</span></div>
            <div class="health-metric"><span class="health-metric-label">Budget Remaining</span><span class="health-metric-value">£${Number(trading.remaining_today_gbp || trading.budget_remaining || 0).toFixed(2)}</span></div>
            <div class="health-metric"><span class="health-metric-label">Trades Today</span><span class="health-metric-value">${trading.trades_today || 0}</span></div>`;
        if (trading.pending_proposals > 0) {
            html += `<div class="health-metric"><span class="health-metric-label">Pending Proposals</span><span class="health-metric-value" style="color:var(--warning)">${trading.pending_proposals}</span></div>`;
        }
    }
    html += `</div>`;

    // ── Scan Loop Card ──
    html += `<div class="health-card">
        <div class="health-card-title">Scan Loop</div>
        <div class="health-metric"><span class="health-metric-label">Scheduler</span><span class="health-metric-value">${scan.scheduler_running ? 'Running' : 'Stopped'}</span></div>`;
    if (scan.scan_running) {
        html += `<div class="health-metric"><span class="health-metric-label">Current</span><span class="health-metric-value" style="color:var(--warning)">Scan in progress</span></div>`;
    }
    if (scan.next_scan) {
        html += `<div class="health-metric"><span class="health-metric-label">Next Scan</span><span class="health-metric-value">${scan.next_scan}</span></div>`;
    }
    if (scan.last_scan) {
        html += `<div class="health-metric"><span class="health-metric-label">Last Scan</span><span class="health-metric-value">${scan.last_scan}</span></div>`;
    }
    html += `</div>`;

    // ── System Resources Card ──
    if (sys.cpu_percent !== undefined) {
        html += `<div class="health-card">
            <div class="health-card-title">System Resources</div>
            ${resourceBar('CPU', sys.cpu_percent)}
            ${resourceBar('Memory', sys.memory_percent)}
            ${resourceBar('Disk', sys.disk_percent)}
        </div>`;
    }

    // ── Cache Card ──
    if (data.cache) {
        html += `<div class="health-card">
            <div class="health-card-title">Cache</div>
            <div class="health-metric"><span class="health-metric-label">Cached Analyses</span><span class="health-metric-value">${data.cache.analysis_entries}</span></div>
        </div>`;
    }

    // ── Uptime Card ──
    html += `<div class="health-card">
        <div class="health-card-title">Server Info</div>
        <div class="health-metric"><span class="health-metric-label">Uptime</span><span class="health-metric-value">${formatUptime(data.uptime_hours)}</span></div>
        <div class="health-metric"><span class="health-metric-label">Status</span><span class="health-metric-value" style="color:var(--success)">${data.status}</span></div>
        <div class="health-metric"><span class="health-metric-label">Timestamp</span><span class="health-metric-value">${new Date(data.timestamp).toLocaleString()}</span></div>
    </div>`;

    cards.innerHTML = html;
}

async function fetchHealth() {
    const indicator = document.getElementById('pollIndicator');
    try {
        const resp = await fetch('/api/health/detail');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        renderHealthPage(data);
        if (indicator) indicator.textContent = `Last updated: ${new Date().toLocaleTimeString()} — refreshing every 10s`;
    } catch (err) {
        console.error('Health fetch failed:', err);
        const dot = document.getElementById('healthDot');
        const bannerText = document.getElementById('healthBannerText');
        const banner = document.getElementById('healthBanner');
        if (dot) dot.className = 'health-dot health-dot--err';
        if (banner) banner.className = 'health-banner health-banner--err';
        if (bannerText) bannerText.textContent = 'Server Unreachable';
        if (indicator) indicator.textContent = `Connection failed — retrying…`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    fetchHealth();
    healthInterval = setInterval(fetchHealth, 10000);
});
