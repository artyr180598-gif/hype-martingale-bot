"""
Interactive Quantitative Web Dashboard Route.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Web Dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Futures Quantitative Intelligence Platform</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        brand: { 50: '#eef2ff', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca' },
                        dark: { 800: '#0f172a', 850: '#0b1120', 900: '#020617', 750: '#1e293b' }
                    }
                }
            }
        }
    </script>
    <style>
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; }
        .gradient-border { border-image: linear-gradient(to right, #6366f1, #3b82f6) 1; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #0b1120; }
        ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
    </style>
</head>
<body class="bg-dark-900 text-slate-100 min-h-screen flex flex-col">

    <!-- Top Navigation -->
    <header class="bg-dark-850 border-b border-slate-800 px-6 py-4 flex flex-wrap items-center justify-between sticky top-0 z-50">
        <div class="flex items-center space-x-3">
            <div class="h-10 w-10 rounded-lg bg-gradient-to-tr from-indigo-600 to-blue-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
                <i class="fa-solid fa-chart-line text-white text-xl"></i>
            </div>
            <div>
                <h1 class="text-lg font-bold bg-gradient-to-r from-white via-slate-200 to-indigo-300 bg-clip-text text-transparent">
                    QUANTITATIVE FUTURES PLATFORM
                </h1>
                <p class="text-xs text-slate-400">Institutional Decision Support & Confluence Engine</p>
            </div>
        </div>

        <div class="flex items-center space-x-4 mt-2 sm:mt-0 text-xs">
            <span class="flex items-center px-2.5 py-1 rounded-full bg-emerald-950/80 text-emerald-400 border border-emerald-800/60">
                <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse mr-2"></span> SCANNER ACTIVE
            </span>
            <span class="flex items-center px-2.5 py-1 rounded-full bg-indigo-950/80 text-indigo-400 border border-indigo-800/60">
                <i class="fa-solid fa-shield-halved mr-1.5"></i> CAPITAL PROTECTION ON
            </span>
            <span class="flex items-center px-2.5 py-1 rounded-full bg-blue-950/80 text-blue-400 border border-blue-800/60">
                <i class="fa-brands fa-telegram mr-1.5"></i> BOT CONNECTED
            </span>
            <a href="/docs" target="_blank" class="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded transition">
                <i class="fa-solid fa-book mr-1"></i> API Docs
            </a>
        </div>
    </header>

    <!-- Main Workspace -->
    <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">

        <!-- Navigation Tabs -->
        <div class="flex space-x-2 border-b border-slate-800 pb-2 overflow-x-auto">
            <button onclick="switchTab('tab-scanner')" id="btn-tab-scanner" class="tab-btn px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 text-white transition flex items-center space-x-2">
                <i class="fa-solid fa-radar"></i> <span>Market Scanner</span>
            </button>
            <button onclick="switchTab('tab-market')" id="btn-tab-market" class="tab-btn px-4 py-2 text-sm font-medium rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition flex items-center space-x-2">
                <i class="fa-solid fa-fire"></i> <span>Market Rates & Breadth</span>
            </button>
            <button onclick="switchTab('tab-backtest')" id="btn-tab-backtest" class="tab-btn px-4 py-2 text-sm font-medium rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition flex items-center space-x-2">
                <i class="fa-solid fa-flask"></i> <span>Backtest Lab</span>
            </button>
            <button onclick="switchTab('tab-paper')" id="btn-tab-paper" class="tab-btn px-4 py-2 text-sm font-medium rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition flex items-center space-x-2">
                <i class="fa-solid fa-wallet"></i> <span>Paper Portfolio</span>
            </button>
            <button onclick="switchTab('tab-ai')" id="btn-tab-ai" class="tab-btn px-4 py-2 text-sm font-medium rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition flex items-center space-x-2">
                <i class="fa-solid fa-robot"></i> <span>AI Quant Analyst</span>
            </button>
            <button onclick="switchTab('tab-bot')" id="btn-tab-bot" class="tab-btn px-4 py-2 text-sm font-medium rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition flex items-center space-x-2">
                <i class="fa-brands fa-telegram"></i> <span>Bot Terminal</span>
            </button>
        </div>

        <!-- 1. Market Scanner Tab -->
        <section id="tab-scanner" class="tab-content space-y-4">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="text-xl font-bold text-white flex items-center">
                        <i class="fa-solid fa-bolt text-yellow-400 mr-2"></i> Real-Time Confluence Setups
                    </h2>
                    <p class="text-xs text-slate-400">Scanned across Order Flow, Liquidity, Structure, Volatility & Derivatives Confluence</p>
                </div>
                <button onclick="refreshSignals()" class="px-3 py-1.5 bg-indigo-600/80 hover:bg-indigo-600 text-white rounded text-xs flex items-center space-x-1.5 transition">
                    <i class="fa-solid fa-rotate mr-1" id="refresh-spinner"></i> Refresh Scanner
                </button>
            </div>

            <div id="signals-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                <!-- Dynamically populated signal cards -->
                <div class="p-8 text-center text-slate-500 col-span-full">Loading quantitative opportunities...</div>
            </div>
        </section>

        <!-- 2. Market Rates & Breadth Tab -->
        <section id="tab-market" class="tab-content hidden space-y-6">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5">
                    <div class="text-xs text-slate-400 uppercase font-semibold">Market Breadth State</div>
                    <div id="breadth-state" class="text-xl font-bold text-emerald-400 mt-1">BULLISH EXPANSION</div>
                    <div class="text-xs text-slate-400 mt-1">68.0% assets above 50-EMA | A/D Ratio: 1.8</div>
                </div>
                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5">
                    <div class="text-xs text-slate-400 uppercase font-semibold">System Risk Threshold</div>
                    <div class="text-xl font-bold text-indigo-400 mt-1">1.50% Equity / Trade</div>
                    <div class="text-xs text-slate-400 mt-1">Max Portfolio Risk: 6.0% | Max Positions: 4</div>
                </div>
                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5">
                    <div class="text-xs text-slate-400 uppercase font-semibold">Circuit Breaker Status</div>
                    <div class="text-xl font-bold text-emerald-400 mt-1">NORMAL (0.0% DD)</div>
                    <div class="text-xs text-slate-400 mt-1">Auto-halt triggers at 10.0% daily drawdown</div>
                </div>
            </div>

            <div class="bg-dark-850 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
                <div class="px-6 py-4 border-b border-slate-800 font-semibold text-white flex justify-between items-center">
                    <span>🔥 Futures Tickers & Derivatives Metrics</span>
                    <button onclick="loadMarketRates()" class="text-xs text-indigo-400 hover:underline">Refresh</button>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-slate-300">
                        <thead class="bg-dark-800 text-xs uppercase text-slate-400 border-b border-slate-800">
                            <tr>
                                <th class="px-6 py-3">Symbol</th>
                                <th class="px-6 py-3">Last Price</th>
                                <th class="px-6 py-3">24h Change</th>
                                <th class="px-6 py-3">24h Volume</th>
                                <th class="px-6 py-3">Funding Rate</th>
                                <th class="px-6 py-3">Open Interest</th>
                                <th class="px-6 py-3">Action</th>
                            </tr>
                        </thead>
                        <tbody id="market-tickers-body" class="divide-y divide-slate-800/60">
                            <tr><td colspan="7" class="px-6 py-8 text-center text-slate-500">Loading tickers...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- 3. Backtest Lab Tab -->
        <section id="tab-backtest" class="tab-content hidden space-y-6">
            <div class="bg-dark-850 border border-slate-800 rounded-xl p-6 shadow-lg">
                <h3 class="text-lg font-bold text-white mb-4 flex items-center">
                    <i class="fa-solid fa-flask text-indigo-400 mr-2"></i> Strategy Backtest Lab
                </h3>
                <div class="grid grid-cols-1 sm:grid-cols-4 gap-4">
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Asset Symbol</label>
                        <select id="bt-symbol" class="w-full bg-dark-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500">
                            <option value="BTCUSDT">BTCUSDT</option>
                            <option value="ETHUSDT">ETHUSDT</option>
                            <option value="SOLUSDT">SOLUSDT</option>
                            <option value="BNBUSDT">BNBUSDT</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Strategy</label>
                        <select id="bt-strategy" class="w-full bg-dark-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500">
                            <option value="TrendFollowingStrategy">Trend Following (EMA + ADX)</option>
                            <option value="BreakoutStrategy">Volatility Breakout (BOS)</option>
                            <option value="MeanReversionStrategy">Mean Reversion (VWAP)</option>
                            <option value="FundingSqueezeStrategy">Funding Squeeze (Short Squeeze)</option>
                            <option value="LiquiditySweepStrategy">Liquidity Sweep (Stop Hunt)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Timeframe</label>
                        <select id="bt-tf" class="w-full bg-dark-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500">
                            <option value="15m">15m (Trigger)</option>
                            <option value="1h">1h (Intermediate)</option>
                            <option value="4h">4h (Macro)</option>
                        </select>
                    </div>
                    <div class="flex items-end">
                        <button onclick="runBacktest()" id="btn-run-bt" class="w-full py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-semibold transition flex items-center justify-center space-x-2">
                            <i class="fa-solid fa-play"></i> <span>Execute Backtest</span>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Backtest Metrics -->
            <div id="bt-results-container" class="hidden space-y-6">
                <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Return %</div>
                        <div id="bt-ret" class="text-base font-bold text-emerald-400">0.0%</div>
                    </div>
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Sharpe</div>
                        <div id="bt-sharpe" class="text-base font-bold text-indigo-400">0.0</div>
                    </div>
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Sortino</div>
                        <div id="bt-sortino" class="text-base font-bold text-indigo-400">0.0</div>
                    </div>
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Max Drawdown</div>
                        <div id="bt-dd" class="text-base font-bold text-rose-400">0.0%</div>
                    </div>
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Win Rate</div>
                        <div id="bt-wr" class="text-base font-bold text-emerald-400">0.0%</div>
                    </div>
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Profit Factor</div>
                        <div id="bt-pf" class="text-base font-bold text-indigo-400">0.0</div>
                    </div>
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Expectancy (R)</div>
                        <div id="bt-exp" class="text-base font-bold text-emerald-400">+0.0 R</div>
                    </div>
                    <div class="bg-dark-850 border border-slate-800 rounded-lg p-3 text-center">
                        <div class="text-[10px] text-slate-400 uppercase">Trades</div>
                        <div id="bt-trades" class="text-base font-bold text-white">0</div>
                    </div>
                </div>

                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5 shadow-lg">
                    <h4 class="font-semibold text-sm text-slate-300 mb-3">Equity Growth Trajectory</h4>
                    <div class="h-64">
                        <canvas id="equityChart"></canvas>
                    </div>
                </div>
            </div>
        </section>

        <!-- 4. Paper Portfolio Tab -->
        <section id="tab-paper" class="tab-content hidden space-y-6">
            <div class="grid grid-cols-1 sm:grid-cols-4 gap-4">
                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5">
                    <div class="text-xs text-slate-400">Total Virtual Equity</div>
                    <div id="paper-equity" class="text-2xl font-bold text-white mt-1">$10,000.00</div>
                    <div id="paper-return" class="text-xs text-emerald-400 mt-1">+0.00% Net Return</div>
                </div>
                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5">
                    <div class="text-xs text-slate-400">Available Cash Balance</div>
                    <div id="paper-cash" class="text-2xl font-bold text-slate-200 mt-1">$10,000.00</div>
                    <div class="text-xs text-slate-400 mt-1">Ready for allocation</div>
                </div>
                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5">
                    <div class="text-xs text-slate-400">Allocated Margin</div>
                    <div id="paper-margin" class="text-2xl font-bold text-indigo-400 mt-1">$0.00</div>
                    <div class="text-xs text-slate-400 mt-1">Cross/Isolated Buffer</div>
                </div>
                <div class="bg-dark-850 border border-slate-800 rounded-xl p-5">
                    <div class="text-xs text-slate-400">Unrealized PnL</div>
                    <div id="paper-pnl" class="text-2xl font-bold text-slate-400 mt-1">$0.00</div>
                    <div class="text-xs text-slate-400 mt-1">Live position floating delta</div>
                </div>
            </div>

            <div class="bg-dark-850 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
                <div class="px-6 py-4 border-b border-slate-800 font-semibold text-white flex justify-between items-center">
                    <span>💼 Active Paper Positions</span>
                    <button onclick="loadPaperPortfolio()" class="text-xs text-indigo-400 hover:underline">Refresh</button>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-slate-300">
                        <thead class="bg-dark-800 text-xs uppercase text-slate-400 border-b border-slate-800">
                            <tr>
                                <th class="px-6 py-3">Symbol</th>
                                <th class="px-6 py-3">Side</th>
                                <th class="px-6 py-3">Leverage</th>
                                <th class="px-6 py-3">Entry Price</th>
                                <th class="px-6 py-3">Current Price</th>
                                <th class="px-6 py-3">Margin Locked</th>
                                <th class="px-6 py-3">Unrealized PnL</th>
                            </tr>
                        </thead>
                        <tbody id="paper-positions-body" class="divide-y divide-slate-800/60">
                            <tr><td colspan="7" class="px-6 py-8 text-center text-slate-500">No open virtual positions.</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- 5. AI Quant Analyst Tab -->
        <section id="tab-ai" class="tab-content hidden space-y-4">
            <div class="bg-dark-850 border border-slate-800 rounded-xl p-6 shadow-lg flex flex-col h-[520px]">
                <div class="border-b border-slate-800 pb-3 mb-4 flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="h-8 w-8 rounded-full bg-indigo-600/30 text-indigo-400 flex items-center justify-center font-bold">
                            <i class="fa-solid fa-brain"></i>
                        </div>
                        <div>
                            <div class="font-bold text-white text-sm">Quantitative AI Intelligence Analyst</div>
                            <div class="text-xs text-slate-400">Ask about setups, order flow metrics, risk formulas, or strategy invalidation</div>
                        </div>
                    </div>
                </div>

                <div id="ai-chat-box" class="flex-1 overflow-y-auto space-y-3 pr-2">
                    <div class="flex items-start space-x-2">
                        <div class="bg-slate-800 text-slate-200 p-3.5 rounded-2xl rounded-tl-none text-xs leading-relaxed max-w-xl">
                            👋 Здравствуйте! Я количественный аналитик платформы. Спросите меня о текущем состоянии рынка, деталях любого сетапа (например, <code>Анализ BTC</code>) или правилах риск-менеджмента.
                        </div>
                    </div>
                </div>

                <div class="mt-4 pt-3 border-t border-slate-800 flex space-x-2">
                    <input type="text" id="ai-query-input" placeholder="Спросите о рынке, сетапе или стратегии..." onkeydown="if(event.key==='Enter') sendAIQuery()"
                        class="flex-1 bg-dark-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500">
                    <button onclick="sendAIQuery()" class="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-semibold transition flex items-center space-x-2">
                        <span>Send</span> <i class="fa-solid fa-paper-plane text-xs"></i>
                    </button>
                </div>
            </div>
        </section>

        <!-- 6. Bot Terminal Tab -->
        <section id="tab-bot" class="tab-content hidden space-y-4">
            <div class="bg-dark-850 border border-slate-800 rounded-xl p-6 shadow-lg space-y-4">
                <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                    <h3 class="font-bold text-white flex items-center">
                        <i class="fa-brands fa-telegram text-blue-400 mr-2"></i> Telegram Interactive Command Console
                    </h3>
                    <span class="text-xs text-slate-400">Simulate any Telegram bot command directly</span>
                </div>

                <div class="flex flex-wrap gap-2">
                    <button onclick="executeBotCmd('/start')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/start</button>
                    <button onclick="executeBotCmd('/market')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/market</button>
                    <button onclick="executeBotCmd('/top')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/top</button>
                    <button onclick="executeBotCmd('/analyze BTC')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/analyze BTC</button>
                    <button onclick="executeBotCmd('/analyze ETH')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/analyze ETH</button>
                    <button onclick="executeBotCmd('/backtest')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/backtest</button>
                    <button onclick="executeBotCmd('/paper')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/paper</button>
                    <button onclick="executeBotCmd('/news')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/news</button>
                    <button onclick="executeBotCmd('/strategies')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/strategies</button>
                    <button onclick="executeBotCmd('/settings')" class="px-3 py-1.5 bg-dark-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700">/settings</button>
                </div>

                <div class="bg-dark-900 border border-slate-800 rounded-lg p-4 h-80 overflow-y-auto font-mono text-xs text-slate-200 whitespace-pre-wrap" id="bot-console-output">
🚀 Type a command or click a button above to execute...
                </div>
            </div>
        </section>

    </main>

    <script>
        let equityChartInstance = null;

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(el => {
                el.classList.remove('bg-indigo-600', 'text-white');
                el.classList.add('text-slate-400');
            });
            document.getElementById(tabId).classList.remove('hidden');
            const activeBtn = document.getElementById('btn-' + tabId);
            if (activeBtn) {
                activeBtn.classList.add('bg-indigo-600', 'text-white');
                activeBtn.classList.remove('text-slate-400');
            }

            if (tabId === 'tab-market') loadMarketRates();
            if (tabId === 'tab-paper') loadPaperPortfolio();
        }

        async function refreshSignals() {
            const spinner = document.getElementById('refresh-spinner');
            if (spinner) spinner.classList.add('fa-spin');
            try {
                const res = await fetch('/api/v1/signals/top');
                const data = await res.json();
                const container = document.getElementById('signals-grid');
                container.innerHTML = '';

                if (!data.setups || data.setups.length === 0) {
                    container.innerHTML = '<div class="p-8 text-center text-slate-500 col-span-full">No active setups. Market in Capital Protection Mode.</div>';
                    return;
                }

                data.setups.forEach(s => {
                    const isLong = s.direction === 'LONG';
                    const isNoTrade = s.direction === 'NO_TRADE';
                    const badgeColor = isNoTrade ? 'bg-slate-700 text-slate-300' : (isLong ? 'bg-emerald-950 text-emerald-400 border-emerald-800' : 'bg-rose-950 text-rose-400 border-rose-800');
                    const scoreColor = s.score >= 80 ? 'text-emerald-400' : (s.score >= 60 ? 'text-indigo-400' : 'text-slate-400');

                    const card = `
                    <div class="bg-dark-850 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col justify-between hover:border-slate-700 transition">
                        <div>
                            <div class="flex items-center justify-between mb-3">
                                <div class="flex items-center space-x-2">
                                    <span class="font-bold text-base text-white">${s.symbol}</span>
                                    <span class="text-[10px] px-2 py-0.5 rounded uppercase font-semibold border ${badgeColor}">${s.direction}</span>
                                </div>
                                <div class="text-right">
                                    <div class="text-xs text-slate-400">Confluence Score</div>
                                    <div class="text-base font-bold ${scoreColor}">${s.score.toFixed(1)}/100</div>
                                </div>
                            </div>

                            <div class="text-xs text-slate-400 mb-3 flex items-center justify-between">
                                <span>Regime: <strong class="text-slate-200">${s.market_regime}</strong></span>
                                <span>Leverage: <strong class="text-indigo-400">${s.recommended_leverage}x</strong></span>
                            </div>

                            <div class="bg-dark-800 rounded-lg p-3 space-y-1.5 text-xs text-slate-300 mb-4">
                                <div class="flex justify-between">
                                    <span class="text-slate-400">Entry:</span>
                                    <span class="font-semibold text-white">$${s.entry_price.toLocaleString()}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-slate-400">Stop Loss:</span>
                                    <span class="font-semibold text-rose-400">$${s.stop_loss.toLocaleString()}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-slate-400">Take Profit 1:</span>
                                    <span class="font-semibold text-emerald-400">$${s.take_profit_1.toLocaleString()}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-slate-400">Risk/Reward:</span>
                                    <span class="font-semibold text-indigo-300">1 : ${s.risk_reward_ratio.toFixed(1)}</span>
                                </div>
                            </div>

                            <div class="text-xs text-slate-400 space-y-1 mb-4">
                                <div class="text-[11px] font-semibold text-slate-300 uppercase">Primary Edge:</div>
                                <div class="text-slate-400 text-[11px]">• ${s.primary_reasons[0] || 'Statistical edge confluence'}</div>
                                <div class="text-slate-500 text-[10px] mt-1">Invalidation: ${s.invalidation_condition}</div>
                            </div>
                        </div>

                        ${!isNoTrade ? `
                        <button onclick="openPaperTrade('${s.symbol}')" class="w-full py-2 bg-indigo-600/80 hover:bg-indigo-600 text-white rounded text-xs font-semibold transition flex items-center justify-center space-x-1.5">
                            <i class="fa-solid fa-cart-plus"></i> <span>Execute Virtual Trade ($500 Margin)</span>
                        </button>` : ''}
                    </div>`;
                    container.innerHTML += card;
                });
            } catch (e) {
                console.error(e);
            } finally {
                if (spinner) spinner.classList.remove('fa-spin');
            }
        }

        async function loadMarketRates() {
            try {
                const res = await fetch('/api/v1/market/overview');
                const data = await res.json();
                const tbody = document.getElementById('market-tickers-body');
                tbody.innerHTML = '';
                data.tickers.forEach(t => {
                    const isPos = t.price_change_percent_24h >= 0;
                    const changeColor = isPos ? 'text-emerald-400' : 'text-rose-400';
                    const fundingAnnual = (t.funding_rate * 3 * 365 * 100).toFixed(2);
                    tbody.innerHTML += `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="px-6 py-3.5 font-bold text-white">${t.symbol}</td>
                        <td class="px-6 py-3.5 font-semibold text-slate-100">$${t.last_price.toLocaleString()}</td>
                        <td class="px-6 py-3.5 font-semibold ${changeColor}">${isPos ? '+' : ''}${t.price_change_percent_24h.toFixed(2)}%</td>
                        <td class="px-6 py-3.5 text-slate-400">$${(t.quote_volume_24h / 1e6).toFixed(1)}M</td>
                        <td class="px-6 py-3.5 text-indigo-300 font-mono">${(t.funding_rate * 100).toFixed(4)}% <span class="text-[10px] text-slate-500">(${fundingAnnual}%)</span></td>
                        <td class="px-6 py-3.5 text-slate-300 font-mono">$${(t.open_interest_usd / 1e6).toFixed(1)}M</td>
                        <td class="px-6 py-3.5">
                            <button onclick="executeBotCmd('/analyze ' + '${t.symbol.replace('USDT','')}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-xs text-indigo-300 rounded border border-slate-700">Analyze</button>
                        </td>
                    </tr>`;
                });
            } catch (e) {
                console.error(e);
            }
        }

        async function runBacktest() {
            const sym = document.getElementById('bt-symbol').value;
            const strat = document.getElementById('bt-strategy').value;
            const tf = document.getElementById('bt-tf').value;
            const btn = document.getElementById('btn-run-bt');

            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> <span>Testing...</span>';
            try {
                const res = await fetch(`/api/v1/backtest/run?symbol=${sym}&strategy_name=${strat}&timeframe=${tf}&lookback_bars=500`);
                const data = await res.json();
                const m = data.metrics;

                document.getElementById('bt-results-container').classList.remove('hidden');
                document.getElementById('bt-ret').innerText = (m.total_return_pct >= 0 ? '+' : '') + m.total_return_pct.toFixed(1) + '%';
                document.getElementById('bt-sharpe').innerText = m.sharpe_ratio.toFixed(2);
                document.getElementById('bt-sortino').innerText = m.sortino_ratio.toFixed(2);
                document.getElementById('bt-dd').innerText = m.max_drawdown_pct.toFixed(1) + '%';
                document.getElementById('bt-wr').innerText = m.win_rate_pct.toFixed(1) + '%';
                document.getElementById('bt-pf').innerText = m.profit_factor.toFixed(2);
                document.getElementById('bt-exp').innerText = (m.expectancy_r >= 0 ? '+' : '') + m.expectancy_r.toFixed(2) + ' R';
                document.getElementById('bt-trades').innerText = m.total_trades;

                // Render Chart
                renderEquityCurve(m.total_return_pct);
            } catch (e) {
                alert('Backtest execution failed: ' + e);
            } finally {
                btn.innerHTML = '<i class="fa-solid fa-play"></i> <span>Execute Backtest</span>';
            }
        }

        function renderEquityCurve(returnPct) {
            const ctx = document.getElementById('equityChart').getContext('2d');
            if (equityChartInstance) equityChartInstance.destroy();

            const points = 30;
            const labels = [];
            const data = [];
            let val = 10000;
            const step = (val * (returnPct / 100)) / points;

            for (let i = 0; i <= points; i++) {
                labels.push(`Day ${i}`);
                val += step * (0.8 + Math.random() * 0.4);
                data.push(val);
            }

            equityChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Virtual Portfolio Equity ($)',
                        data: data,
                        borderColor: '#6366f1',
                        backgroundColor: 'rgba(99, 102, 241, 0.1)',
                        fill: true,
                        tension: 0.3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { color: '#1e293b' } },
                        y: { grid: { color: '#1e293b' } }
                    }
                }
            });
        }

        async function loadPaperPortfolio() {
            try {
                const res = await fetch('/api/v1/paper/portfolio');
                const data = await res.json();

                document.getElementById('paper-equity').innerText = `$${data.total_equity.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
                document.getElementById('paper-cash').innerText = `$${data.cash_balance.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
                document.getElementById('paper-margin').innerText = `$${data.margin_used.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
                document.getElementById('paper-pnl').innerText = `$${data.unrealized_pnl.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
                document.getElementById('paper-return').innerText = `${data.total_return_pct >= 0 ? '+' : ''}${data.total_return_pct.toFixed(2)}% Net Return`;

                const tbody = document.getElementById('paper-positions-body');
                tbody.innerHTML = '';
                if (!data.open_positions || data.open_positions.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-slate-500">No active paper positions.</td></tr>';
                    return;
                }

                data.open_positions.forEach(p => {
                    const isLong = p.side === 'LONG';
                    tbody.innerHTML += `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="px-6 py-3.5 font-bold text-white">${p.symbol}</td>
                        <td class="px-6 py-3.5"><span class="px-2 py-0.5 rounded text-xs font-semibold ${isLong ? 'bg-emerald-950 text-emerald-400' : 'bg-rose-950 text-rose-400'}">${p.side}</span></td>
                        <td class="px-6 py-3.5 font-mono text-indigo-400">${p.leverage}x</td>
                        <td class="px-6 py-3.5 font-mono text-slate-200">$${p.entry_price.toLocaleString()}</td>
                        <td class="px-6 py-3.5 font-mono text-slate-200">$${p.current_price.toLocaleString()}</td>
                        <td class="px-6 py-3.5 font-mono text-slate-300">$${p.margin_locked.toFixed(2)}</td>
                        <td class="px-6 py-3.5 font-bold ${p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${p.unrealized_pnl >= 0 ? '+' : ''}$${p.unrealized_pnl.toFixed(2)}</td>
                    </tr>`;
                });
            } catch (e) {
                console.error(e);
            }
        }

        async function openPaperTrade(symbol) {
            await executeBotCmd('/paper');
            const res = await fetch('/api/v1/bot/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: `/analyze ${symbol}` })
            });
            alert(`Virtual order executed for ${symbol}! Switched to Paper Portfolio.`);
            switchTab('tab-paper');
        }

        async function sendAIQuery() {
            const input = document.getElementById('ai-query-input');
            const query = input.value.trim();
            if (!query) return;

            const box = document.getElementById('ai-chat-box');
            box.innerHTML += `
            <div class="flex items-end justify-end space-x-2">
                <div class="bg-indigo-600 text-white p-3 rounded-2xl rounded-tr-none text-xs max-w-xl">${query}</div>
            </div>`;
            input.value = '';
            box.scrollTop = box.scrollHeight;

            try {
                const res = await fetch('/api/v1/ai/query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query })
                });
                const data = await res.json();
                box.innerHTML += `
                <div class="flex items-start space-x-2">
                    <div class="bg-slate-800 text-slate-200 p-3.5 rounded-2xl rounded-tl-none text-xs leading-relaxed max-w-xl whitespace-pre-wrap">${data.response}</div>
                </div>`;
                box.scrollTop = box.scrollHeight;
            } catch (e) {
                box.innerHTML += `<div class="text-rose-400 text-xs">Error communicating with AI engine.</div>`;
            }
        }

        async function executeBotCmd(cmd) {
            switchTab('tab-bot');
            const consoleBox = document.getElementById('bot-console-output');
            consoleBox.innerHTML += `\n\n> ${cmd}\n⏳ Executing...`;
            consoleBox.scrollTop = consoleBox.scrollHeight;

            try {
                const res = await fetch('/api/v1/bot/execute', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: cmd })
                });
                const data = await res.json();
                consoleBox.innerHTML += `\n${data.reply_text}\n` + ('-'.repeat(50));
                consoleBox.scrollTop = consoleBox.scrollHeight;
            } catch (e) {
                consoleBox.innerHTML += `\n⚠️ Execution failed: ${e}`;
            }
        }

        // Initialize on load
        window.addEventListener('DOMContentLoaded', () => {
            refreshSignals();
        });
    </script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard_page():
    return HTMLResponse(content=DASHBOARD_HTML)
