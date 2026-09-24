import asyncio
import json
import logging
import os
import time
import aiohttp
from aiohttp import web

from src.strategies.pump_scanner import PumpScanner

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
        for task in (self.task, self.monitor_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await self.scanner.stop()
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
                    # Alert-only by design: send at most three strongest fresh signals per cycle.
                    # This prevents a volatile market from flooding the Telegram chat.
                    signals = [s for s in signals if s.quality_score >= self.scanner.settings.min_signal_score]
                    signals = sorted(signals, key=lambda s: (s.quality_score, abs(s.change_pct)), reverse=True)[:3]
                    for signal in signals:
                        try:
                            await self._send(self.chat_id, self.scanner.format_signal(signal))
                        except Exception as exc:
                            log.warning('Automatic signal delivery paused: %s', exc)
                            break
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
        for signal in signals:
            await self._send(chat_id, self.scanner.format_signal(signal))

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
            f'6. Минимальное качество авто-сигнала: {s.min_signal_score}/100\n\n'
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
