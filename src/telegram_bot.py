import asyncio
import logging
import os
import aiohttp

from src.strategies.pump_scanner import PumpScanner

log = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, hub):
        self.hub = hub
        self.token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.offset = 0
        self.running = False
        self.session = None
        self.task = None
        self.monitor_task = None
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

    async def _api(self, method, payload=None):
        url = 'https://api.telegram.org/bot{}/{}'.format(self.token, method)
        async with self.session.post(url, json=payload or {}, timeout=aiohttp.ClientTimeout(total=35)) as response:
            data = await response.json()
            if not data.get('ok'):
                raise RuntimeError('Telegram API request failed')
            return data.get('result')

    async def start(self):
        if not self.token or not self.chat_id:
            raise RuntimeError('TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN and TELEGRAM_CHAT_ID are required')
        self.session = aiohttp.ClientSession()
        await self.scanner.start()
        self.running = True
        await self._api('deleteWebhook', {'drop_pending_updates': False})
        await self._api('setMyCommands', {'commands': [
            {'command': 'start', 'description': 'Открыть меню'},
            {'command': 'scan', 'description': 'Проверить Pump/Dump'},
            {'command': 'settings', 'description': 'Настройки фильтров'},
            {'command': 'health', 'description': 'Проверить данные'},
        ]})
        await self._send(self.chat_id, 'Pump/Dump Monitor на Bybit\n\nМониторинг запущен. Выбери действие ниже.', keyboard=True)
        self.task = asyncio.create_task(self._poll(), name='telegram-poll')
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
        if self.session and not self.session.closed:
            await self.session.close()

    async def _monitor_loop(self):
        while self.running:
            try:
                signals = await self.scanner.update()
                for signal in signals:
                    await self._send(self.chat_id, self.scanner.format_signal(signal))
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception('Pump monitor cycle failed')
            await asyncio.sleep(5)

    async def _poll(self):
        while self.running:
            try:
                updates = await self._api('getUpdates', {'offset': self.offset, 'timeout': 25, 'allowed_updates': ['message']})
                for update in updates or []:
                    self.offset = int(update['update_id']) + 1
                    await self._handle(update)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning('Telegram poll failed: %s', type(exc).__name__)
                await asyncio.sleep(3)

    def _allowed(self, update):
        message = update.get('message') or {}
        chat = message.get('chat') or {}
        return str(chat.get('id', '')) == str(self.chat_id)

    async def _send(self, chat_id, text, keyboard=False):
        payload = {'chat_id': chat_id, 'text': text}
        if keyboard:
            payload['reply_markup'] = self.menu_keyboard
        await self._api('sendMessage', payload)

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
            f'5. Типы сигналов: {s.signal_types}\n\n'
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

