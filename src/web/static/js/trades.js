// Trade Journal & Live Trading Functions
// Extracted from trades.html inline script for merged dashboard

// ─── Debate Panel ────────────────────────────────────

function renderDebatePanel(p) {
    const d = p.debate_data;
    if (!d || (!d.bull_text && !d.bear_text && !d.referee_text)) return '';

    const bullConv = Number(d.bull_conviction || 0);
    const bearConv = Number(d.bear_conviction || 0);
    const total = bullConv + bearConv;
    const bullPct = total > 0 ? Math.round((bullConv / total) * 100) : 50;
    const regime = d.regime || '';

    const truncate = (text, len) => {
        if (!text) return 'No data.';
        return text.length > len ? text.slice(0, len) + '...' : text;
    };

    const regimeBadge = regime
        ? `<span class="debate-regime-badge debate-regime-${regime}">${regime.toUpperCase()}</span>`
        : '';

    const bullLabel = bullConv ? ` ${bullConv}%` : '';
    const bearLabel = bearConv ? ` ${bearConv}%` : '';

    return `
        <div class="debate-toggle" onclick="var b=this.nextElementSibling;b.style.display=b.style.display==='none'?'block':'none'">
            View debate ${regimeBadge}<span style="color:#48bb78">Bull${bullLabel}</span> vs <span style="color:#fc8181">Bear${bearLabel}</span>
        </div>
        <div class="debate-body" style="display:none">
            <div class="debate-conviction-bar">
                <div class="debate-bull-bar" style="width:${bullPct}%"></div>
                <div class="debate-bear-bar" style="width:${100 - bullPct}%"></div>
            </div>
            <div class="debate-section bull">
                <div class="debate-section-label">Bull Case</div>
                <div class="debate-section-text">${escapeHtml(truncate(d.bull_text, 500))}</div>
            </div>
            <div class="debate-section bear">
                <div class="debate-section-label">Bear Case</div>
                <div class="debate-section-text">${escapeHtml(truncate(d.bear_text, 500))}</div>
            </div>
            <div class="debate-section referee">
                <div class="debate-section-label">Referee</div>
                <div class="debate-section-text">${escapeHtml(truncate(d.referee_text, 500))}</div>
            </div>
        </div>
    `;
}

// ─── Live Trading Functions ──────────────────────────

async function loadTradingStatus(data = null) {
    try {
        if (!data) {
            const response = await fetch('/api/trades/status', { headers: authHeaders() });
            data = await response.json();
        }

        document.getElementById('budgetRemaining').textContent = `£${(data.remaining_today_gbp ?? 0).toFixed(2)}`;
        document.getElementById('tradesToday').textContent = data.trades_today || 0;

        const statusEl = document.getElementById('tradingStatus');
        if (!data.active) {
            statusEl.textContent = 'HALTED';
            statusEl.style.background = 'rgba(245,101,101,0.2)';
            statusEl.style.color = '#fc8181';
            document.getElementById('killSwitchBtn').textContent = 'Resume';
        } else {
            statusEl.textContent = 'ACTIVE';
            statusEl.style.background = 'rgba(72,187,120,0.2)';
            statusEl.style.color = '#48bb78';
            document.getElementById('killSwitchBtn').textContent = 'Kill Switch';
        }

    } catch (e) {
        console.error('Error loading trading status:', e);
    }
}

async function loadPendingProposals(prefetchedProposals = null) {
    try {
        let data;
        if (prefetchedProposals !== null) {
            data = { proposals: prefetchedProposals };
        } else {
            const response = await fetch('/api/trades/pending', { headers: authHeaders() });
            data = await response.json();
        }
        const section = document.getElementById('pendingSection');
        const container = document.getElementById('pendingProposals');

        if (data.proposals && data.proposals.length > 0) {
            section.style.display = 'block';
            container.innerHTML = data.proposals.map(p => {
                const sideColor = p.side === 'buy' ? '#48bb78' : '#fc8181';
                const created = new Date(p.created_at).toLocaleString();
                return `
                    <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-left: 3px solid ${sideColor}; border-radius: 8px; padding: 10px; margin-bottom: 8px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <div>
                                <span style="font-weight: 700; font-size: 13px; color: ${sideColor};">${escapeHtml(p.side.toUpperCase())}</span>
                                <span style="font-weight: 700; font-size: 13px; margin-left: 4px;">${escapeHtml(p.symbol)}</span>
                                <span style="color: var(--text-secondary); font-size: 11px; margin-left: 6px;">£${Number(p.amount_gbp).toFixed(2)}</span>
                            </div>
                            <div style="display: flex; gap: 4px;">
                                <button onclick="approveTrade('${escapeHtml(p.id)}')" style="padding: 4px 10px; background: #38a169; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: 600; font-size: 11px;">Approve</button>
                                <button onclick="rejectTrade('${escapeHtml(p.id)}')" style="padding: 4px 10px; background: #e53e3e; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: 600; font-size: 11px;">Reject</button>
                            </div>
                        </div>
                        <div style="font-size: 12px; color: var(--text-secondary); line-height: 1.5;">
                            <div style="margin-bottom: 4px;">${escapeHtml(p.reason)}</div>
                            <div>Confidence: ${Number(p.confidence)}% • Price: £${Number(p.price_at_proposal).toFixed(6)} • ${created}</div>
                        </div>
                        ${renderDebatePanel(p)}
                    </div>
                `;
            }).join('');
        } else {
            section.style.display = 'none';
        }
    } catch (e) {
        console.error('Error loading proposals:', e);
    }
}

function formatPrice(price) {
    if (price === null || price === undefined || price === 0) return '—';
    const n = Number(price);
    if (n >= 100)          return `£${n.toFixed(2)}`;
    if (n >= 0.01)         return `£${n.toFixed(4)}`;
    if (n >= 0.000001)     return `£${n.toFixed(6)}`;
    const decimals = -Math.floor(Math.log10(n)) + 2;
    return `£${n.toFixed(Math.min(decimals, 12))}`;
}

// Smart GBP formatter — uses extra decimal places for sub-penny values
function smartGbp(value) {
    const n = Number(value) || 0;
    const abs = Math.abs(n);
    if (abs === 0) return '£0.00';
    if (abs >= 0.01) return `£${n.toFixed(2)}`;
    if (abs >= 0.0001) return `£${n.toFixed(4)}`;
    return `£${n.toFixed(6)}`;
}

async function loadExecutedTrades() {
    try {
        const hdrs = { headers: authHeaders() };
        const [execRes, histRes] = await Promise.all([
            fetch('/api/trades/history', hdrs),
            fetch('/api/portfolio/history?limit=100', hdrs)
        ]);
        const execData = await execRes.json();
        const histData = await histRes.json();
        const container = document.getElementById('tradeLogUnified');

        const allTrades = [];
        const seen = new Set();

        // Dedup key: prefer proposal_id (UUID), then order_id, then symbol+side+normalised timestamp
        function dedupeKey(t) {
            if (t.proposal_id) return `pid:${t.proposal_id}`;
            if (t.order_id)    return `oid:${t.order_id}`;
            // Normalise timestamp to seconds-precision UTC for fallback comparison
            const ts = new Date(t.timestamp).toISOString().slice(0, 19);
            return `${t.symbol}|${t.side}|${ts}`;
        }

        // Engine trades first (they lack realised_pnl_gbp)
        if (execData.trades) {
            execData.trades.forEach(t => {
                const key = dedupeKey(t);
                seen.add(key);
                allTrades.push({
                    symbol: t.symbol,
                    side: t.side,
                    quantity: t.quantity,
                    price: t.price,
                    amount_gbp: t.amount_gbp,
                    fee_gbp: t.fee_gbp || 0,
                    exchange: t.exchange || '—',
                    timestamp: t.timestamp,
                    reasoning: t.reason || t.reasoning || '',
                    realised_pnl_gbp: t.realised_pnl_gbp,
                    source: 'engine',
                });
            });
        }

        // Portfolio trades — only add if not already seen
        if (histData.trades) {
            histData.trades.forEach(t => {
                const key = dedupeKey(t);
                if (seen.has(key)) {
                    // Merge realised P&L from portfolio into the existing engine entry
                    if (t.realised_pnl_gbp !== undefined && t.realised_pnl_gbp !== null) {
                        const existing = allTrades.find(e => dedupeKey(e) === key);
                        if (existing && existing.realised_pnl_gbp == null) {
                            existing.realised_pnl_gbp = t.realised_pnl_gbp;
                        }
                    }
                    return;
                }
                seen.add(key);
                allTrades.push({
                    symbol: t.symbol,
                    side: t.side,
                    quantity: t.quantity || 0,
                    price: t.price || 0,
                    amount_gbp: t.amount_gbp || 0,
                    fee_gbp: t.fee_gbp || 0,
                    exchange: t.exchange || '—',
                    timestamp: t.timestamp,
                    reasoning: t.reasoning || '',
                    realised_pnl_gbp: t.realised_pnl_gbp,
                    source: 'portfolio',
                });
            });
        }

        allTrades.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

        // Limit to most recent 15 trades
        const displayTrades = allTrades.slice(0, 15);

        if (displayTrades.length > 0) {
            container.innerHTML = displayTrades.map(t => {
                const isBuy = t.side === 'buy';
                const sideColor = isBuy ? '#48bb78' : '#fc8181';
                const time = new Date(t.timestamp).toLocaleString();
                const priceDisplay = (t.price && t.price > 0) ? `£${Number(t.price).toFixed(6)}` : '—';
                const pnlStr = t.realised_pnl_gbp !== undefined && t.realised_pnl_gbp !== null
                    ? ` • P&L: ${t.realised_pnl_gbp >= 0 ? '+' : ''}£${t.realised_pnl_gbp.toFixed(2)}`
                    : '';
                return `
                    <div style="display: flex; gap: 8px; align-items: flex-start; padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.03); font-size: 12px;">
                        <div style="flex: 1;">
                            <span style="font-weight: 700;">${escapeHtml(t.symbol)}</span>
                            <span style="color: ${sideColor}; font-weight: 600; margin-left: 6px;">${escapeHtml(t.side.toUpperCase())}</span>
                            <span style="color: var(--text-secondary);"> ${(t.quantity || 0).toFixed(6)} @ ${priceDisplay}</span>
                            <div style="color: var(--text-secondary); font-size: 11px; margin-top: 3px;">
                                £${(t.amount_gbp || 0).toFixed(2)} • ${escapeHtml(t.exchange)} • Fee: £${(t.fee_gbp || 0).toFixed(2)}${pnlStr}
                            </div>
                            ${t.reasoning ? `<div style="color: var(--text-secondary); font-size: 11px; margin-top: 2px; font-style: italic;">${escapeHtml('"' + t.reasoning.substring(0, 120) + (t.reasoning.length > 120 ? '…' : '') + '"')}</div>` : ''}
                        </div>
                        <span style="color: var(--text-secondary); font-size: 11px; white-space: nowrap;">${time}</span>
                    </div>
                `;
            }).join('');
        } else {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 20px; font-size: 13px;">No trades yet. Agents will propose trades when they find high-conviction opportunities.</p>';
        }
    } catch (e) {
        console.error('Error loading trade log:', e);
    }
}

async function approveTrade(proposalId) {
    if (!confirm('Approve and execute this trade?')) return;
    try {
        const response = await fetch(`/api/trades/approve/${proposalId}`, {
            method: 'POST',
            headers: authHeadersJson(),
        });
        const data = await response.json();
        if (data.success) {
            showTradeAlert(`Trade approved — ${data.side?.toUpperCase()} ${data.symbol}`, 'success');
        } else {
            showTradeAlert(data.error || 'Could not approve', 'error');
        }
        setTimeout(() => {
            loadPendingProposals();
            loadExecutedTrades();
            loadTradingStatus();
            loadTradesPortfolio();
        }, 1000);
    } catch (e) {
        showTradeAlert('Error approving trade: ' + e.message, 'error');
    }
}

async function rejectTrade(proposalId) {
    if (!confirm('Reject this trade proposal?')) return;
    try {
        const response = await fetch(`/api/trades/reject/${proposalId}`, {
            method: 'POST',
            headers: authHeadersJson(),
        });
        const data = await response.json();
        if (data.success) {
            showTradeAlert('Trade rejected', 'success');
        } else {
            showTradeAlert(data.error || 'Could not reject', 'error');
        }
        loadPendingProposals();
    } catch (e) {
        showTradeAlert('Error rejecting trade: ' + e.message, 'error');
    }
}

async function toggleKillSwitch() {
    const btn = document.getElementById('killSwitchBtn');
    const isHalted = btn.textContent.includes('Resume');
    const action = isHalted ? 'deactivate' : 'activate';

    if (!isHalted && !confirm('This will HALT all trading and reject pending proposals. Continue?')) return;

    // If no API key stored, fall back to the PIN-based /pause page
    if (!getApiKey()) {
        window.location.href = '/pause';
        return;
    }

    try {
        const res = await fetch('/api/trades/kill-switch', {
            method: 'POST',
            headers: authHeadersJson(),
            body: JSON.stringify({action})
        });
        if (res.status === 401 || res.status === 403) {
            // Key stored but rejected — offer the /pause page as fallback
            if (confirm('API key rejected. Open the PIN-based pause page instead?')) {
                window.open('/pause', '_blank');
            }
            return;
        }
        if (!res.ok) {
            showTradeAlert('Server error — could not toggle kill switch', 'error');
            return;
        }
        showTradeAlert(isHalted ? 'Trading resumed' : 'Trading HALTED — all pending proposals rejected', isHalted ? 'success' : 'error');
        loadTradingStatus();
        loadPendingProposals();
    } catch (e) {
        showTradeAlert('Error toggling kill switch: ' + e.message, 'error');
    }
}

// ─── Scan Functions ───────────────────────────────────

async function loadScanStatusDetail(prefetchedData = null) {
    try {
        const data = prefetchedData || await fetch('/api/trades/scan-status', { headers: authHeaders() }).then(r => r.json());
        const status = data.status || {};

        const statusEl = document.getElementById('scanStatusDetail');
        if (status.scan_running) {
            statusEl.textContent = 'Running';
            statusEl.style.background = 'rgba(237,137,54,0.2)';
            statusEl.style.color = '#ed8936';
        } else if (status.scheduler_active) {
            statusEl.textContent = 'Scheduled';
            statusEl.style.background = 'rgba(72,187,120,0.2)';
            statusEl.style.color = '#48bb78';
        } else {
            statusEl.textContent = 'Off';
            statusEl.style.background = 'rgba(255,255,255,0.05)';
            statusEl.style.color = 'var(--text-secondary)';
        }

        if (status.next_scan) {
            const next = new Date(status.next_scan);
            const now = new Date();
            const diffMs = next - now;
            if (diffMs > 0) {
                const diffH = Math.floor(diffMs / 3600000);
                const diffM = Math.floor((diffMs % 3600000) / 60000);
                document.getElementById('scanNextTime').textContent = diffH > 0 ? `${diffH}h ${diffM}m` : `${diffM}m`;
            } else {
                document.getElementById('scanNextTime').textContent = 'Soon';
            }
        } else if (status.scan_interval_hours && status.scan_interval_hours > 0) {
            document.getElementById('scanNextTime').textContent = `Every ${status.scan_interval_hours}h`;
        } else {
            document.getElementById('scanNextTime').textContent = status.scan_time || '—';
        }
        document.getElementById('scanLastRun').textContent = status.last_scan
            ? new Date(status.last_scan).toLocaleString()
            : 'Never';

        const logs = data.recent_logs || [];
        if (logs.length > 0) {
            const latest = logs[logs.length - 1];
            document.getElementById('scanCoinsAnalysed').textContent = latest.coins_analysed || 0;
            document.getElementById('scanProposals').textContent = latest.proposals_made || 0;
        }
    } catch (e) {
        console.error('Error loading scan status:', e);
    }
}

async function triggerScan() {
    const btn = document.getElementById('scanNowBtn');
    btn.disabled = true;
    btn.textContent = 'Scanning...';
    const resultEl = document.getElementById('scanResultMsg');

    try {
        const response = await fetch('/api/trades/scan-now', {
            method: 'POST',
            headers: authHeadersJson(),
        });
        const data = await response.json();

        resultEl.style.display = 'block';
        if (data.success) {
            resultEl.style.borderColor = 'var(--success)';
            resultEl.style.background = 'rgba(72,187,120,0.1)';
            resultEl.innerHTML = `Scan complete — <strong>${Number(data.coins_analysed)}</strong> coins, <strong>${Number(data.proposals_made)}</strong> proposals, <strong>${Number(data.errors?.length || 0)}</strong> errors`;
        } else {
            resultEl.style.borderColor = 'var(--error)';
            resultEl.style.background = 'rgba(245,101,101,0.1)';
            resultEl.innerHTML = escapeHtml(data.error || 'Scan failed');
        }

        loadScanStatusDetail();
        loadPendingProposals();
    } catch (e) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = 'Network error';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Scan Now';
    }
}

// ─── Portfolio Functions ──────────────────────────────

async function refreshTradesPortfolio(callerBtn) {
    // callerBtn may be passed directly or found by ID (trades.html has the id)
    const btn = callerBtn || document.getElementById('portfolioRefreshBtn');
    const origText = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = 'Refreshing...'; }
    try {
        await loadTradesPortfolio();
        if (btn) {
            btn.textContent = 'Updated';
            setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 1500);
        }
    } catch (e) {
        if (btn) {
            btn.textContent = 'Error';
            setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
        }
    }
}

async function loadTradesPortfolio() {
    if (!document.getElementById('holdingsList')) return; // Holdings panel not present on this page
    try {
        const response = await fetch('/api/portfolio/holdings', { headers: authHeaders() });
        const data = await response.json();
        const holdings = data.holdings || [];
        holdings.sort((a, b) => (b.unrealised_pnl_gbp ?? 0) - (a.unrealised_pnl_gbp ?? 0));
        const summary = data.summary || {};

        document.getElementById('tpValue').textContent = `£${(summary.total_value_gbp || 0).toFixed(2)}`;
        document.getElementById('tpCount').textContent = summary.active_holdings || 0;
        document.getElementById('tpTotalTrades').textContent = summary.total_trades || 0;
        document.getElementById('tpFees').textContent = `£${(summary.total_fees_gbp || 0).toFixed(2)}`;

        const now = new Date();
        document.getElementById('portfolioUpdated').textContent = `Updated ${now.toLocaleTimeString()}`;

        const pnlEl = document.getElementById('tpPnl');
        const unrealisedPnl = summary.unrealised_pnl_gbp || 0;
        pnlEl.textContent = `${unrealisedPnl >= 0 ? '+' : '-'}£${Math.abs(unrealisedPnl).toFixed(2)}`;
        pnlEl.style.color = unrealisedPnl >= 0 ? 'var(--success)' : 'var(--error)';

        const realisedEl = document.getElementById('tpRealisedPnl');
        const realisedPnl = summary.realised_pnl_gbp || 0;
        realisedEl.textContent = `${realisedPnl >= 0 ? '+' : '-'}£${Math.abs(realisedPnl).toFixed(2)}`;
        realisedEl.style.color = realisedPnl >= 0 ? 'var(--success)' : 'var(--error)';

        const container = document.getElementById('holdingsList');

        if (holdings.length > 0) {
            container.innerHTML = `<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px;">` + holdings.map(h => {
                const pnlPct = h.unrealised_pnl_pct || 0;
                const pnlGbp = h.unrealised_pnl_gbp || 0;
                const isUp = pnlGbp >= 0;
                const pnlColor = isUp ? '#48bb78' : '#fc8181';
                const pnlBg = isUp ? 'rgba(72,187,120,0.1)' : 'rgba(252,129,129,0.1)';
                const borderAccent = isUp ? 'rgba(72,187,120,0.3)' : 'rgba(252,129,129,0.3)';
                const arrow = isUp ? '▲' : '▼';
                const barFill = Math.min(Math.abs(pnlPct), 50) / 50 * 50; // max 50% each side
                const barLeft  = isUp ? 50 : (50 - barFill);
                const barRight = isUp ? (50 + barFill) : 50;
                const barWidthPct = (barRight - barLeft).toFixed(1);
                const barOffsetPct = barLeft.toFixed(1);
                return `
                <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-top: 3px solid ${borderAccent}; border-radius: 10px; padding: 16px; display: flex; flex-direction: column; gap: 0;">
                    <!-- Header -->
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <div>
                                <span style="font-size: 18px; font-weight: 700; color: var(--text-primary);">${escapeHtml(h.symbol)}</span>
                                ${h.coin_name && h.coin_name !== h.symbol ? `<div style="font-size: 11px; color: var(--text-secondary); margin-top: 1px;">${escapeHtml(h.coin_name)}</div>` : ''}
                            </div>
                            <span style="font-size: 10px; color: var(--text-secondary); background: rgba(255,255,255,0.06); border: 1px solid var(--border); border-radius: 4px; padding: 2px 6px;">${escapeHtml(h.exchange || '—')}</span>
                        </div>
                        <span style="font-size: 12px; font-weight: 600; color: ${pnlColor}; background: ${pnlBg}; padding: 3px 10px; border-radius: 6px;">${arrow} ${Math.abs(pnlPct).toFixed(1)}%</span>
                    </div>
                    <!-- Value + P&L on same line -->
                    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                        <span style="font-size: 20px; font-weight: 700; color: var(--text-primary);">${smartGbp(h.current_value_gbp || 0)}</span>
                        <span style="font-size: 14px; font-weight: 700; color: ${pnlColor};">${pnlGbp >= 0 ? '+' : '-'}${smartGbp(Math.abs(pnlGbp))}</span>
                    </div>
                    <!-- Centred gain/loss bar -->
                    <div style="position: relative; height: 4px; background: rgba(255,255,255,0.08); border-radius: 2px; overflow: hidden; margin-bottom: 4px;">
                        <div style="position: absolute; left: 50%; top: 0; width: 1px; height: 100%; background: rgba(255,255,255,0.2);"></div>
                        <div style="position: absolute; left: ${barOffsetPct}%; top: 0; height: 100%; width: ${barWidthPct}%; background: ${pnlColor}; border-radius: 2px;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 9px; color: var(--text-secondary); margin-bottom: 12px;"><span>loss</span><span>break-even</span><span>gain</span></div>
                    <!-- Avg buy vs live price -->
                    <div style="border-top: 1px solid rgba(255,255,255,0.06); padding-top: 10px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px;">
                        <div>
                            <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px;">Avg Buy</div>
                            <div style="font-size: 13px; font-weight: 600; color: var(--text-primary);">${formatPrice(h.avg_entry_price)}</div>
                        </div>
                        <div>
                            <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px;">${h.price_stale ? 'Est. Price' : 'Live Price'}</div>
                            <div style="font-size: 13px; font-weight: 600; color: ${h.price_stale ? 'var(--text-secondary)' : (h.current_price ? pnlColor : 'var(--text-primary)')};">${h.current_price ? formatPrice(h.current_price) + (h.price_stale ? ' (est.)' : '') : '—'}</div>
                        </div>
                    </div>
                    <!-- Invested + Holdings -->
                    <div style="border-top: 1px solid rgba(255,255,255,0.06); padding-top: 10px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                        <div>
                            <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px;">Invested</div>
                            <div style="font-size: 13px; font-weight: 600; color: var(--text-primary);">${smartGbp(h.position_cost_gbp ?? h.total_cost_gbp ?? 0)}</div>
                        </div>
                        <div>
                            <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px;">Holdings</div>
                            <div style="font-size: 13px; font-weight: 600; color: var(--text-primary);">${(h.quantity || 0).toFixed(4)}</div>
                        </div>
                    </div>
                </div>`;
            }).join('') + `</div>`;
        } else {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 20px; font-size: 13px;">No holdings yet. The portfolio tracker records every executed trade automatically.</p>';
        }
    } catch (e) {
        console.error('Error loading portfolio:', e);
    }
}

// ─── Market State ────────────────────────────────────

async function loadMarketState() {
    const el = document.getElementById('fngDisplay');
    const headlinesEl = document.getElementById('marketHeadlines');
    const statsEl = document.getElementById('globalStats');
    try {
        const response = await fetch('/api/market/state');
        const data = await response.json();

        if (data.error || data.current_value === null) {
            el.innerHTML = '<span style="color: var(--text-secondary); font-size: 13px;">Unavailable</span>';
            headlinesEl.innerHTML = '';
            return;
        }

        const val = data.current_value;
        const cls = data.classification;
        const trend = data.trend || '';

        let color, bg;
        if (val <= 20) { color = '#fc8181'; bg = 'rgba(252,129,129,0.12)'; }
        else if (val <= 35) { color = '#f6ad55'; bg = 'rgba(246,173,85,0.12)'; }
        else if (val <= 55) { color = '#ecc94b'; bg = 'rgba(236,201,75,0.12)'; }
        else if (val <= 75) { color = '#68d391'; bg = 'rgba(104,211,145,0.12)'; }
        else { color = '#48bb78'; bg = 'rgba(72,187,120,0.12)'; }

        let trendIcon = '';
        if (trend === 'IMPROVING') trendIcon = ' ↗';
        else if (trend === 'DETERIORATING') trendIcon = ' ↘';
        else if (trend === 'STABLE') trendIcon = ' →';

        let sparkline = '';
        if (data.history_7d && data.history_7d.length > 1) {
            const vals = data.history_7d.map(h => h.value).reverse();
            const max = Math.max(...vals, 1);
            sparkline = `<div style="display: flex; align-items: flex-end; gap: 2px; height: 20px; margin-left: 4px;">` +
                vals.map(v => {
                    const h = Math.max(3, (v / max) * 20);
                    let barColor;
                    if (v <= 20) barColor = '#fc8181';
                    else if (v <= 35) barColor = '#f6ad55';
                    else if (v <= 55) barColor = '#ecc94b';
                    else if (v <= 75) barColor = '#68d391';
                    else barColor = '#48bb78';
                    return `<div style="width: 4px; height: ${h}px; background: ${barColor}; border-radius: 1px; opacity: 0.7;"></div>`;
                }).join('') + `</div>`;
        }

        el.innerHTML = `
            <div style="display: flex; align-items: center; gap: 8px; background: ${bg}; border: 1px solid ${color}33; border-radius: 8px; padding: 6px 12px;">
                <span style="font-size: 22px; font-weight: 700; color: ${color};">${val}</span>
                <div>
                    <div style="font-size: 13px; font-weight: 600; color: ${color};">${cls}${trendIcon}</div>
                </div>
                ${sparkline}
            </div>
            <span style="font-size: 12px; color: var(--text-secondary); font-style: italic;">${escapeHtml(data.trading_signal || '')}</span>
        `;

        const gs = data.global_stats || {};
        if (gs.btc_dominance) {
            const mcapChange = gs.market_cap_change_24h || 0;
            const mcapColor = mcapChange >= 0 ? '#68d391' : '#fc8181';
            const mcapArrow = mcapChange >= 0 ? '▲' : '▼';
            statsEl.innerHTML = `
                <span style="font-size: 12px; color: var(--text-secondary);">BTC ${gs.btc_dominance}%</span>
                <span style="font-size: 11px; color: rgba(255,255,255,0.15);">|</span>
                <span style="font-size: 12px; color: ${mcapColor};">${mcapArrow} ${Math.abs(mcapChange).toFixed(1)}% 24h</span>
            `;
        }

        const headlines = data.headlines || [];
        if (headlines.length > 0) {
            headlinesEl.innerHTML = `
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    ${headlines.map(h => {
                        const safeTitle = escapeHtml(h.title || '');
                        const safeSource = escapeHtml(h.source || '');
                        const safeLink = encodeURI(h.link || '#');
                        return `
                        <div style="display: flex; align-items: flex-start; gap: 8px; line-height: 1.4;">
                            <span style="color: rgba(255,255,255,0.15); font-size: 11px; flex-shrink: 0; margin-top: 2px;">●</span>
                            <div>
                                <a href="${safeLink}" target="_blank" rel="noopener noreferrer"
                                   style="font-size: 12px; color: var(--text-secondary); text-decoration: none; transition: color 0.15s;"
                                   onmouseover="this.style.color='var(--text-primary)'"
                                   onmouseout="this.style.color='var(--text-secondary)'">
                                    ${safeTitle}
                                </a>
                                <span style="font-size: 10px; color: rgba(255,255,255,0.2); margin-left: 6px;">${safeSource}</span>
                            </div>
                        </div>
                    `;}).join('')}
                </div>
            `;
        } else {
            headlinesEl.innerHTML = '<div style="color: var(--text-secondary); font-size: 12px;">No headlines available</div>';
        }
    } catch (e) {
        el.innerHTML = '<span style="color: var(--text-secondary); font-size: 13px;">Unavailable</span>';
        headlinesEl.innerHTML = '';
        console.error('Error loading market state:', e);
    }
}

// ─── RL Insights ─────────────────────────────────────

async function loadRlInsights() {
    try {
        const response = await fetch('/api/rl/insights');
        const data = await response.json();
        const insights = data.insights || [];
        const record   = data.record   || {};
        const patterns = data.patterns || {};
        const container = document.getElementById('rlInsights');

        if (insights.length === 0) {
            container.innerHTML = '<p class="empty-state">No patterns yet — insights appear after trades complete.</p>';
            return;
        }

        // ── Win/loss record header ──────────────────────────────
        let recordHtml = '';
        if (record.total > 0) {
            const winRate = Math.round((record.wins / record.total) * 100);
            recordHtml = `
                <div class="rl-record">
                    <span class="rl-record__item rl-record__item--win">${record.wins}W</span>
                    <span class="rl-record__divider">/</span>
                    <span class="rl-record__item rl-record__item--loss">${record.losses}L</span>
                    <span class="rl-record__winrate">${winRate}% win rate</span>
                </div>`;
        }

        // ── Best / worst patterns ───────────────────────────────
        let patternHtml = '';
        if (patterns.best || patterns.worst) {
            patternHtml = '<div class="rl-patterns">';
            if (patterns.best) {
                patternHtml += `
                    <div class="rl-pattern rl-pattern--best">
                        <div class="rl-pattern__label">Best setup</div>
                        <div class="rl-pattern__value">${escapeHtml(patterns.best.label)}</div>
                    </div>`;
            }
            if (patterns.worst) {
                patternHtml += `
                    <div class="rl-pattern rl-pattern--worst">
                        <div class="rl-pattern__label">Avoid</div>
                        <div class="rl-pattern__value">${escapeHtml(patterns.worst.label)}</div>
                    </div>`;
            }
            patternHtml += '</div>';
        }

        // ── Insight cards (filter out W/L record line — shown above) ──
        const categorised = insights
            .filter(text => !/^Recent record:/.test(text))
            .map(text => {
                const lower = text.toLowerCase();
                if (/loss|losses|underperform|worst|penalty|avoid|mistake/.test(lower)) {
                    return { text, type: 'avoid',   tag: 'AVOID'   };
                } else if (/winner|winning|wins|promis|lean toward|worked|profit/.test(lower)) {
                    return { text, type: 'signal',  tag: 'SIGNAL'  };
                }
                return { text, type: 'pattern', tag: 'PATTERN' };
            });

        const sections = [
            { type: 'signal',  label: "What's Working", items: categorised.filter(i => i.type === 'signal') },
            { type: 'avoid',   label: 'What to Avoid',  items: categorised.filter(i => i.type === 'avoid') },
            { type: 'pattern', label: 'Patterns',        items: categorised.filter(i => i.type === 'pattern') },
        ].filter(s => s.items.length > 0);

        const sectionsHtml = sections.map(({ type, label, items }) => `
            <div class="rl-section rl-section--${type}">
                <div class="rl-section__label">${label}</div>
                <ul class="rl-list">
                    ${items.map(({ text }) => `<li class="rl-list__item">${escapeHtml(text)}</li>`).join('')}
                </ul>
            </div>
        `).join('');

        container.innerHTML = `
            ${recordHtml}
            ${patternHtml}
            ${sectionsHtml}
        `;
    } catch (e) {
        console.error('Error loading RL insights:', e);
    }
}

// ─── Closed Positions ────────────────────────────────

async function loadClosedPositions() {
    try {
        const response = await fetch('/api/portfolio/closed', { headers: authHeaders() });
        const data = await response.json();
        const positions = data.positions || [];
        const container = document.getElementById('closedPositions');

        if (positions.length > 0) {
            container.innerHTML = positions.map(p => {
                const won = p.won;
                const pnlColor = won ? '#48bb78' : '#fc8181';
                const closedDate = p.closed_at ? new Date(p.closed_at).toLocaleDateString() : '—';
                return `
                    <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-left: 3px solid ${pnlColor}; border-radius: 8px; padding: 14px; margin-bottom: 8px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <span style="font-weight: 700; font-size: 15px;">${escapeHtml(p.symbol)}</span>
                                <span style="color: var(--text-secondary); font-size: 12px; margin-left: 8px;">${p.trades} trades</span>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-weight: 700; color: ${pnlColor}; font-size: 14px;">${p.realised_pnl_gbp >= 0 ? '+' : ''}£${p.realised_pnl_gbp.toFixed(2)}</div>
                                <div style="font-size: 11px; color: var(--text-secondary);">Cost: £${p.total_cost_gbp.toFixed(2)} • Fees: £${p.total_fees_gbp.toFixed(2)}</div>
                            </div>
                        </div>
                        <div style="font-size: 11px; color: var(--text-secondary); margin-top: 6px;">
                            Exchange: ${escapeHtml(p.exchange || '—')} • Closed: ${closedDate}
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 20px; font-size: 13px;">No closed positions yet.</p>';
        }
    } catch (e) {
        console.error('Error loading closed positions:', e);
    }
}

// ─── Trade Log ───────────────────────────────────────

async function loadTradeLog() {
    await loadExecutedTrades();
}

// ─── Activity Log ────────────────────────────────────

// escapeHtml is defined in utils.js

function truncate(str, max) {
    if (!str || str.length <= max) return str;
    return str.slice(0, max) + '…';
}

async function loadMonthlyReview() {
    try {
        const response = await fetch('/api/portfolio/monthly-review', { headers: authHeaders() });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const months = data.months || [];
        const container = document.getElementById('monthlyReview');
        if (!container) return;

        if (months.length === 0) {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 20px; font-size: 13px;">No trade history yet — monthly stats will appear here once trades are recorded.</p>';
            return;
        }

        const rows = months.map(m => {
            const label = new Date(m.month + '-01').toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
            const pnl = m.realised_pnl_gbp;
            const pnlColor = pnl > 0 ? '#48bb78' : pnl < 0 ? '#fc8181' : 'var(--text-secondary)';
            const pnlSign = pnl > 0 ? '+' : '';
            const winRate = m.win_rate_pct !== null ? m.win_rate_pct.toFixed(1) + '%' : '—';
            const winRateColor = m.win_rate_pct !== null ? (m.win_rate_pct >= 50 ? '#48bb78' : '#fc8181') : 'var(--text-secondary)';
            const best = m.best_trade ? `<span style="color:#48bb78">${escapeHtml(m.best_trade.symbol)} +£${m.best_trade.pnl_gbp.toFixed(2)}</span>` : '—';
            const worst = m.worst_trade ? `<span style="color:#fc8181">${escapeHtml(m.worst_trade.symbol)} £${m.worst_trade.pnl_gbp.toFixed(2)}</span>` : '—';
            return `<tr style="border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 13px;">
                <td style="padding: 10px 8px; font-weight: 600; color: var(--text-primary); white-space: nowrap;">${label}</td>
                <td style="padding: 10px 8px; text-align: center; color: var(--text-secondary);">${m.buys}</td>
                <td style="padding: 10px 8px; text-align: center; color: var(--text-secondary);">${m.sells}</td>
                <td style="padding: 10px 8px; text-align: right; color: var(--text-secondary);">£${m.invested_gbp.toFixed(2)}</td>
                <td style="padding: 10px 8px; text-align: right; font-weight: 700; color: ${pnlColor};">${pnlSign}£${pnl.toFixed(2)}</td>
                <td style="padding: 10px 8px; text-align: center; font-weight: 600; color: ${winRateColor};">${winRate}</td>
                <td style="padding: 10px 8px; text-align: center; color: var(--text-secondary);">${m.unique_coins}</td>
                <td style="padding: 10px 8px; text-align: center;">${best}</td>
                <td style="padding: 10px 8px; text-align: center;">${worst}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `
            <div style="overflow-x: auto;">
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.08);">
                        <th style="padding: 8px; text-align: left; font-weight: 600;">Month</th>
                        <th style="padding: 8px; text-align: center; font-weight: 600;">Buys</th>
                        <th style="padding: 8px; text-align: center; font-weight: 600;">Sells</th>
                        <th style="padding: 8px; text-align: right; font-weight: 600;">Invested</th>
                        <th style="padding: 8px; text-align: right; font-weight: 600;">Realised P&L</th>
                        <th style="padding: 8px; text-align: center; font-weight: 600;">Win Rate</th>
                        <th style="padding: 8px; text-align: center; font-weight: 600;">Coins</th>
                        <th style="padding: 8px; text-align: center; font-weight: 600;">Best Trade</th>
                        <th style="padding: 8px; text-align: center; font-weight: 600;">Worst Trade</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            </div>`;
    } catch (e) {
        console.error('Error loading monthly review:', e);
        const container = document.getElementById('monthlyReview');
        if (container) container.innerHTML = '<p style="color: #fc8181; text-align: center; padding: 20px; font-size: 13px;">Failed to load monthly review.</p>';
    }
}

async function loadActivityLog(prefetchedData = null) {
    try {
        let data;
        if (prefetchedData !== null) {
            data = prefetchedData;
        } else {
            const response = await fetch('/api/trades/audit-trail?limit=20', { headers: authHeaders() });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            data = await response.json();
        }
        const entries = data.entries || [];
        const container = document.getElementById('activityLog');

        if (entries.length > 0) {
            container.innerHTML = entries.map(e => {
                const time = new Date(e.timestamp).toLocaleString();
                let color = 'var(--text-secondary)';
                if (e.event === 'proposal') { color = '#667eea'; }
                else if (e.event === 'sell_proposal') { color = '#ed8936'; }
                else if (e.event === 'scan_start') { color = '#ed8936'; }
                else if (e.event === 'scan_complete') { color = '#48bb78'; }
                else if (e.event === 'trade_executed') { color = '#48bb78'; }
                else if (e.event === 'trade_failed') { color = '#fc8181'; }
                else if (e.event === 'error') { color = '#fc8181'; }
                else if (e.event === 'scan_no_coins') { color = '#ecc94b'; }
                else if (e.event === 'budget_exhausted') { color = '#ecc94b'; }

                let detail = '';
                if (e.symbol) detail += `<strong>${escapeHtml(e.symbol)}</strong> `;
                if (e.side) detail += `${escapeHtml(e.side.toUpperCase())} `;
                if (e.amount_gbp) detail += `£${Number(e.amount_gbp).toFixed(2)} `;
                if (e.price && Number(e.price) > 0) detail += `@ £${Number(e.price).toFixed(6)} `;
                if (e.exchange) detail += `on ${escapeHtml(e.exchange)} `;
                if (e.reason) detail += `— ${escapeHtml(truncate(e.reason, 120))} `;
                if (e.confidence) detail += `(${Number(e.confidence)}% conf) `;
                if (e.proposals !== undefined) detail += `${Number(e.proposals)} proposals `;
                if (e.error) detail += `— ${escapeHtml(truncate(e.error, 120))} `;

                return `<div style="display: flex; gap: 10px; align-items: flex-start; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.03); font-size: 13px;">
                    <div style="flex: 1;">
                        <span style="color: ${color}; font-weight: 600;">${escapeHtml(e.event)}</span>
                        <span style="color: var(--text-secondary);"> ${detail}</span>
                    </div>
                    <span style="color: var(--text-secondary); font-size: 11px; white-space: nowrap;">${time}</span>
                </div>`;
            }).join('');
        } else {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 20px; font-size: 13px;">No activity yet. Activity will appear here when scans run and trades are proposed.</p>';
        }
    } catch (e) {
        console.error('Error loading activity log:', e);
        const container = document.getElementById('activityLog');
        if (container && container.textContent.includes('Loading')) {
            container.innerHTML = '<p style="color: #fc8181; text-align: center; padding: 20px; font-size: 13px;">Failed to load activity log.</p>';
        }
    }
}

// ─── Stats ───────────────────────────────────────────

async function loadTradeStats() {
    try {
        const response = await fetch('/api/portfolio/performance', { headers: authHeaders() });
        const data = await response.json();

        document.getElementById('totalTrades').textContent = data.total_trades || 0;
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// ─── Alert ───────────────────────────────────────────

function showTradeAlert(message, type = 'success') {
    const alertEl = document.getElementById('tradeAlert');
    const messageEl = document.getElementById('tradeAlertMessage');
    if (!alertEl || !messageEl) return;
    
    messageEl.textContent = message;
    alertEl.className = `alert ${type} show`;
    
    setTimeout(() => {
        alertEl.classList.remove('show');
    }, 5000);
}

// ─── Init Trading Sections ───────────────────────────

function initTradingSections() {
    loadTradeStats();
    loadTradingStatus();
    loadPendingProposals();
    loadExecutedTrades();
    loadScanStatusDetail();
    loadTradesPortfolio();
    loadMarketState();
    loadClosedPositions();
    loadRlInsights();
    loadMonthlyReview();
    loadActivityLog();

    // Periodic refresh — most sidebar sections are driven by SSE (startDashboardSSE).
    // Only keep timers for data not included in the SSE bundle.
    setInterval(loadTradesPortfolio, 120000);  // live P&L — needs exchange prices, 2 min
    setInterval(loadMarketState, 600000);       // market state — low cadence, keep as-is
}
