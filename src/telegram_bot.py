import asyncio
import logging
import os
import aiohttp
# Railway deployment verification: runtime logic unchanged.
from src.strategies.confluence import ConfluenceAnalyzer

log = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self, hub):
        self.hub = hub
        self.token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.analyzer = ConfluenceAnalyzer(hub, int(os.getenv('SIGNAL_MIN_SCORE', '70')))
        self.offset = 0
        self.running = False
        self.session = None
        self.task = None
        self.menu_keyboard = {'keyboard': [[{'text': '🔎 Сканировать'}, {'text': '₿ BTC'}], [{'text': '🌊 Order Flow'}, {'text': '❤️ Health'}], [{'text': '📋 Меню'}]], 'resize_keyboard': True, 'is_persistent': True}

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
        self.running = True
        await self._api('deleteWebhook', {'drop_pending_updates': False})
        await self._api('setMyCommands', {'commands': [
            {'command': 'start', 'description': 'Открыть меню'},
            {'command': 'menu', 'description': 'Показать меню'},
            {'command': 'scan', 'description': 'Сканировать рынок'},
            {'command': 'btc', 'description': 'BTC'},
            {'command': 'flow', 'description': 'Order Flow'},
            {'command': 'health', 'description': 'Проверить данные'},
        ]})
        await self._send(self.chat_id, 'HyperData Signal Terminal\\n\\nВыбери действие ниже.', keyboard=True)
        self.task = asyncio.create_task(self._poll(), name='telegram-poll')

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.session and not self.session.closed:
            await self.session.close()

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
        aliases = {'🔎 Сканировать': 'scan', '₿ BTC': 'btc', '🌊 Order Flow': 'flow', '❤️ Health': 'health', '📋 Меню': 'menu'}
        command = aliases.get(command, command).lower()
        if command.startswith('/start') or command in {'menu', '/menu'}:
            await self._send(chat_id, 'HyperData Signal Terminal\\n\\nВыбери действие ниже. Данные берутся из live-источников.', keyboard=True)
        elif command in {'scan', '/scan'}:
            await self._scan(chat_id)
        elif command in {'btc', '/btc'}:
            await self._btc(chat_id)
        elif command in {'flow', '/flow'}:
            await self._flow(chat_id)
        elif command in {'health', '/health'}:
            await self._health(chat_id)
        elif command in {'help', '/help'}:
            await self._send(chat_id, 'Доступно: Сканировать, BTC, Order Flow, Health.', keyboard=True)
        else:
            await self._send(chat_id, 'Команда не распознана. Нажми «📋 Меню».', keyboard=True)

    async def _scan(self, chat_id):
        await self._send(chat_id, 'Scanning live HyperData feeds...')
        signals = await self.analyzer.scan(limit=5)
        if not signals:
            await self._send(chat_id, 'No signal passed the configured filter. Weak or incomplete data is not converted into a signal.')
            return
        for s in signals:
            text = ('{} {} | score {}/100\\nEntry {:.6g}-{:.6g}\\nSL {:.6g}\\nTP1 {:.6g} TP2 {:.6g} TP3 {:.6g}\\nReasons: {}\\nWarnings: {}\\nAnalysis only; no orders are placed.').format(s.direction, s.symbol, s.score, s.entry_low, s.entry_high, s.stop, s.tp1, s.tp2, s.tp3, '; '.join(s.reasons[:4]), '; '.join(s.warnings[:3]) or 'none')
            await self._send(chat_id, text)

    async def _btc(self, chat_id):
        a = self.hub.market.assets.get('BTC')
        if not a:
            await self._send(chat_id, 'BTC data is not ready.')
            return
        await self._send(chat_id, 'BTC {:.6g} | 24h {:+.2f}% | OI ${:,.0f} | HL funding {:+.5f}%'.format(a.price, a.price_change_24h_pct * 100, a.open_interest, a.funding_rate * 100))

    async def _flow(self, chat_id):
        lines = ['BTC order flow']
        for tf in ('5m', '15m', '1h'):
            snap = self.hub.orderflow.get_snapshot('BTC', tf)
            lines.append('{}: {}'.format(tf, 'OFI {:+.2f}, CVD ${:,.0f}, {}'.format(snap.ofi, snap.cvd, snap.signal) if snap else 'unavailable'))
        await self._send(chat_id, '\\n'.join(lines))

    async def _health(self, chat_id):
        health = self.hub.health.latest()
        await self._send(chat_id, 'Data health: {}'.format(health.get('overall') if health else 'initializing'))
