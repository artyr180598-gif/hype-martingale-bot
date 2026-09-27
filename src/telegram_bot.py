import asyncio
import json
import logging
import os
import time
import aiohttp
from aiohttp import web

from src.strategies.pump_scanner import PumpScanner
from src.strategies.signal_confirmation import SignalConfirmation

log = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, hub):
        self.hub = hub
        self.token = (os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN', '')).strip()
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
        self.offset = 0
        self.running = False
        self.session = None
        self.task = None
        self.monitor_task = None
        self.web_runner = None
        self.send_lock = asyncio.Lock()
        self.telegram_cooldown_until = 0.0
        self.last_telegram_cooldown_log = 0.0
        self.scanner = PumpScanner()
        self.confirmation = SignalConfirmation()
        self.confirmation_tasks: set[asyncio.Task] = set()
        # Delivery layer: keep scanning unchanged, but aggregate/rank alerts before Telegram.
        self.recent_alerts: dict[tuple[str, str], dict] = {}
        self.global_alert_times: list[float] = []
        self.alert_aggregation_seconds = 8.0
        self.per_symbol_cooldown_seconds = 12 * 60
        self.global_alert_window_seconds = 10 * 60
        self.global_alert_cap = 3
        self.menu_keyboard = {
            'keyboard': [
                [{'text': '🔎 Сканировать'}, {'text': '⚙️ Настройки'}],
                [{'text': '🟢 Pump'}, {'text': '🔴 Dump'}],
                [{'text': '🔄 Оба'}, {'text': '❤️ Health'}],
            ],
            'resize_keyboard': True,
            'is_persistent': True,
        }

    async def _api(self, method, payload=None, retries=3):
        if not self.token:
            raise RuntimeError('Telegram token is empty')
        url = 'https://api.telegram.org/bot{}/{}'.format(self.token, method)
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                async with self.session.post(
                    url,
                    json=payload or {},
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as response:
                    raw = await response.text()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {}
                    if data.get('ok'):
                        return data.get('result')
                    description = str(data.get('description') or raw[:300]).replace(self.token, '<TOKEN>')
                    error = f'Telegram API {method} HTTP {response.status}: {description}'
                    if response.status == 429:
                        retry_after = int((data.get('parameters') or {}).get('retry_after') or 60)
                        self.telegram_cooldown_until = max(self.telegram_cooldown_until, time.monotonic() + retry_after)
                        log.error('Telegram flood control: %s; pausing outbound messages for %ss', error, retry_after)
                        # Rate-limit is not a network failure. Pause outbound traffic without crashing the worker.
                        return None
                    last_error = RuntimeError(error)
                    log.error('%s (attempt %s/%s)', error, attempt, retries)
                    if response.status in {400, 401, 403, 404}:
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                log.warning('Telegram API %s connection failed (attempt %s/%s): %s', method, attempt, retries, type(exc).__name__)
            if attempt < retries:
                await asyncio.sleep(min(2 * attempt, 6))
        raise last_error or RuntimeError(f'Telegram API {method} failed')

    async def start(self):
        if not self.token or not self.chat_id:
            raise RuntimeError('TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN and TELEGRAM_CHAT_ID are required')
        self.session = aiohttp.ClientSession()
        await self.scanner.start()
        await self.confirmation.start()
        self.running = True

        # Use Telegram webhook instead of getUpdates polling. The previous logs showed
        # HTTP 409 from another getUpdates consumer, so polling is unsafe here.
        domain = (os.getenv('RAILWAY_PUBLIC_DOMAIN') or 'worker-production-29abc.up.railway.app').strip()
        app = web.Application()
        app.router.add_post('/telegram/webhook', self._webhook)
        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, '0.0.0.0', int(os.getenv('PORT', '8080'))).start()
        await self._api('deleteWebhook', {'drop_pending_updates': False})
        await self._api('setWebhook', {'url': 'https://' + domain + '/telegram/webhook', 'drop_pending_updates': False})
        await self._api('setMyCommands', {'commands': [
            {'command': 'start', 'description': 'Открыть меню'},
            {'command': 'scan', 'description': 'Проверить Pump/Dump'},
            {'command': 'settings', 'description': 'Настройки фильтров'},
            {'command': 'health', 'description': 'Проверить данные'},
        ]})
        # Do not send a startup message: restarting the worker must never create a Telegram flood.
        self.task = None
        self.monitor_task = asyncio.create_task(self._monitor_loop(), name='pump-monitor')

    async def stop(self):
        self.running = False
        for task in (self.task, self.monitor_task, *self.confirmation_tasks):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await self.scanner.stop()
        await self.confirmation.stop()
        self.confirmation_tasks.clear()
        if self.web_runner:
            try:
                await self._api('deleteWebhook', {'drop_pending_updates': False})
            except Exception:
                pass
            await self.web_runner.cleanup()
            self.web_runner = None
        if self.session and not self.session.closed:
            await self.session.close()

    async def _monitor_loop(self):
        while self.running:
            try:
                signals = await self.scanner.update()
                if signals:
                    await self._process_auto_candidates(signals)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception('Pump monitor cycle failed')
            await asyncio.sleep(5)

    async def _webhook(self, request):
        try:
            update = await request.json()
            await self._handle(update)
            return web.Response(text='ok')
        except Exception:
            log.exception('Telegram webhook update failed')
            return web.Response(text='error', status=500)

    def _allowed(self, update):
        message = update.get('message') or {}
        chat = message.get('chat') or {}
        return str(chat.get('id', '')) == str(self.chat_id)

    async def _send(self, chat_id, text, keyboard=False):
        # Telegram allows bursts only within limits. Serialize all sends and keep
        # a conservative 1.2s gap so automatic scanning cannot flood the chat.
        async with self.send_lock:
            now = time.monotonic()
            if now < self.telegram_cooldown_until:
                remaining = int(self.telegram_cooldown_until - now)
                if now - self.last_telegram_cooldown_log >= 30:
                    log.warning('Telegram outbound cooldown active: %ss remaining', remaining)
                    self.last_telegram_cooldown_log = now
                return False
            if hasattr(self, '_next_send_at'):
                wait = self._next_send_at - now
                if wait > 0:
                    await asyncio.sleep(wait)
            payload = {'chat_id': chat_id, 'text': text}
            if keyboard:
                payload['reply_markup'] = self.menu_keyboard
            try:
                result = await self._api('sendMessage', payload, retries=1)
                if result is None:
                    return False
                self._next_send_at = time.monotonic() + 1.2
                return True
            except RuntimeError as exc:
                if 'HTTP 429' in str(exc):
                    return False
                raise

    async def _handle(self, update):
        if not self._allowed(update):
            return
        message = update['message']
        chat_id = message['chat']['id']
        command = (message.get('text') or '').strip()
        aliases = {
            '🔎 Сканировать': 'scan',
            '⚙️ Настройки': 'settings',
            '🟢 Pump': 'pump',
            '🔴 Dump': 'dump',
            '🔄 Оба': 'both',
            '❤️ Health': 'health',
        }
        command = aliases.get(command, command).lower()

        if command.startswith('/start') or command in {'menu', '/menu'}:
            await self._send(chat_id, 'Pump/Dump Monitor на Bybit\n\nМониторинг работает автоматически.', keyboard=True)
        elif command in {'scan', '/scan', 'pump', 'dump', 'both'}:
            if command == 'pump':
                self.scanner.settings.signal_types = 'PUMP'
                self.scanner.settings.save()
            elif command == 'dump':
                self.scanner.settings.signal_types = 'DUMP'
                self.scanner.settings.save()
            elif command == 'both':
                self.scanner.settings.signal_types = 'BOTH'
                self.scanner.settings.save()
            await self._manual_scan(chat_id)
        elif command in {'settings', '/settings'}:
            await self._settings(chat_id)
        elif command in {'health', '/health'}:
            await self._health(chat_id)
        else:
            await self._send(chat_id, 'Нажми «⚙️ Настройки» или «🔎 Сканировать».', keyboard=True)

    def _prune_alert_state(self, now: float) -> None:
        cutoff = now - self.global_alert_window_seconds
        self.global_alert_times = [t for t in self.global_alert_times if t >= cutoff]

    def _already_alerted(self, signal, now: float) -> bool:
        state = self.recent_alerts.get((signal.symbol, signal.direction))
        if not state:
            return False
        age = now - state["sent_at"]
        if age >= self.per_symbol_cooldown_seconds:
            return False
        # A materially stronger move may update the existing alert before cooldown expires.
        return not (
            abs(signal.change_pct - state["change_pct"]) >= 1.0
            or signal.quality_score - state["quality_score"] >= 12
        )

    @staticmethod
    def _combined_rank(signal, confirmation_result):
        confirmation_score = confirmation_result.score if confirmation_result else 0
        # Do not replace the primary score: confirmation is an independent second dimension.
        # Movement gets a capped bonus so a very late/large move cannot win on size alone.
        move_bonus = min(12.0, max(0.0, abs(signal.change_pct) - 3.0) * 2.0)
        verdict_bonus = {
            "СИЛЬНОЕ ПРОДОЛЖЕНИЕ": 8,
            "ПРОДОЛЖЕНИЕ ВЕРОЯТНО": 4,
            "СМЕШАННО / ЖДАТЬ": 0,
            "ПРОДОЛЖЕНИЕ НЕ ПОДТВЕРЖДЕНО": -4,
            "ПРОВЕРКА НЕ ПОЛУЧЕНА": -8,
        }.get(getattr(confirmation_result, "verdict", ""), -8)
        return signal.quality_score * 0.55 + confirmation_score * 0.35 + move_bonus + verdict_bonus

    def _format_ranked_signal(self, signal, confirmation_result, rank: int) -> str:
        base = self.scanner.format_signal(signal)
        if confirmation_result is None:
            return base + "\n\n🔎 Вторая проверка: данные не получены — ничего не выдумываем."
        m = confirmation_result.metrics
        lines = [
            "",
            "━━━━━━━━━━━━",
            f"🔎 Независимая проверка: {confirmation_result.score}/100 · {confirmation_result.verdict}",
        ]
        if confirmation_result.reasons:
            lines += ["✅ " + x for x in confirmation_result.reasons[:4]]
        if confirmation_result.warnings:
            lines += ["⚠️ " + x for x in confirmation_result.warnings[:3]]
        if m:
            lines.append(
                f"📐 ADX 5m {m.get('adx_5m', -1):.1f} · "
                f"Volume 1m {m.get('volume_ratio', 0):.1f}x · "
                f"OI {m.get('oi_change_pct', -999):+.2f}%"
            )
        lines.append("")
        lines.append(f"📌 Радар-рейтинг: {rank}/100")
        lines.append("⚠️ Это фильтр качества, а не гарантия движения.")
        return base + "\n" + "\n".join(lines)

    async def _rank_and_send(self, signals, chat_id):
        now = time.monotonic()
        self._prune_alert_state(now)
        eligible = []
        for signal in signals:
            if signal.quality_score < self.scanner.settings.min_signal_score:
                continue
            if self._already_alerted(signal, now):
                log.info("Radar suppressed duplicate %s %s %.2f%%", signal.symbol, signal.direction, signal.change_pct)
                continue
            eligible.append(signal)

        if not eligible:
            return

        # Collect independent confirmation before delivery. This replaces the old
        # 2-3 Telegram messages per signal with one enriched alert.
        checks = await asyncio.gather(
            *(self.confirmation.check(s.symbol, s.direction) for s in eligible),
            return_exceptions=True,
        )
        ranked = []
        for signal, check in zip(eligible, checks):
            result = None if isinstance(check, Exception) else check
            ranked.append((self._combined_rank(signal, result), signal, result))
        ranked.sort(key=lambda x: x[0], reverse=True)

        # Hard global cap is deliberately small; candidates remain in logs/Radar state
        # instead of flooding the chat.
        available_slots = max(0, self.global_alert_cap - len(self.global_alert_times))
        if available_slots <= 0:
            for _, signal, _ in ranked:
                log.info("Radar suppressed by global cap: %s %s", signal.symbol, signal.direction)
            return

        for rank_value, signal, result in ranked[:available_slots]:
            # A short aggregation pause lets simultaneous candidates be ranked together
            # without delaying the monitor cycle for long.
            await asyncio.sleep(0)
            rank_display = max(0, min(100, round(rank_value)))
            sent = await self._send(
                chat_id,
                self._format_ranked_signal(signal, result, rank_display),
            )
            if sent:
                self.recent_alerts[(signal.symbol, signal.direction)] = {
                    "sent_at": now,
                    "change_pct": signal.change_pct,
                    "quality_score": signal.quality_score,
                    "radar_score": rank_display,
                }
                self.global_alert_times.append(now)

    async def _process_auto_candidates(self, signals):
        try:
            await self._rank_and_send(signals, self.chat_id)
        except Exception as exc:
            log.warning("Automatic Radar delivery failed: %s", type(exc).__name__)

    async def _manual_scan(self, chat_id):
        await self._send(chat_id, 'Проверяю Bybit linear USDT рынок...')
        try:
            signals = await asyncio.wait_for(self.scanner.scan_once(), timeout=25)
        except asyncio.TimeoutError:
            await self._send(chat_id, 'Проверка не завершилась за 25 секунд. Сигнал не придумываю.')
            return
        if not signals:
            await self._send(chat_id, 'Сейчас нет нового Pump/Dump, прошедшего выбранные фильтры.')
            return
        await self._rank_and_send(signals, chat_id)

    async def _settings(self, chat_id):
        s = self.scanner.settings
        rsi_tfs = ', '.join(s.rsi_timeframes)
        text = (
            '⚙️ Настройки\n\n'
            f'1. Интервал мониторинга: {s.interval_seconds // 60} мин\n'
            f'2. Порог изменения цены: {s.threshold_pct:.2f}%\n'
            f'3. RSI: {"ON" if s.rsi_enabled else "OFF"} ({rsi_tfs}), уровни {s.rsi_overbought:.0f}/{s.rsi_oversold:.0f}\n'
            f'4. Рост/падение 24ч: {"ON" if s.day_filter_enabled else "OFF"} ({s.day_min_pct:.1f}%)\n'
            f'5. Типы сигналов: {s.signal_types}\n'
            f'6. Минимальное качество авто-сигнала: {s.min_signal_score}/100\n'
            '7. Вторая проверка: структура 5m + ADX/DI + наклон ADX + EMA + объём + OI + ATR + стакан + RSI\n\n'
            f'Доп. данные: дисбаланс {"ON" if s.show_imbalance else "OFF"}, объём {"ON" if s.show_volume else "OFF"}, funding {"ON" if s.show_funding else "OFF"}, листинг {"ON" if s.show_listing else "OFF"}.\n\n'
            'Базовая логика: цена должна пройти порог относительно минимума/максимума внутри интервала.'
        )
        await self._send(chat_id, text, keyboard=True)

    async def _health(self, chat_id):
        try:
            tickers = await self.scanner.fetch_tickers()
            await self._send(chat_id, f'Bybit Pump Monitor: LIVE\nИнструментов USDT: {len(tickers)}\nПорог: {self.scanner.settings.threshold_pct:.2f}%\nИнтервал: {self.scanner.settings.interval_seconds // 60} мин')
        except Exception as exc:
            await self._send(chat_id, f'Bybit Pump Monitor: ERROR ({type(exc).__name__})')
