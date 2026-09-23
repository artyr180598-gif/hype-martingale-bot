import asyncio
import os
import signal
import subprocess
import time
from typing import Any

import requests
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

API = "http://127.0.0.1:8080/api/v1"
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
API_USER = os.environ.get("FREQTRADE__API_SERVER__USERNAME", "freqtrade")
API_PASSWORD = os.environ["FREQTRADE__API_SERVER__PASSWORD"]

MENU = [
    ["📊 Открытые сделки", "💰 Баланс"],
    ["📈 Результаты", "📜 История"],
    ["🟢 Запустить", "⏸ Пауза"],
    ["⛔ Остановить", "🔄 Обновить"],
    ["ℹ️ Как работает", "⚙️ Настройки"],
]

class FT:
    def __init__(self):
        self.access = None
        self.refresh = None
        self.expires_at = 0

    def login(self):
        r = requests.post(f"{API}/token/login", auth=(API_USER, API_PASSWORD), timeout=10)
        r.raise_for_status()
        data = r.json()
        self.access = data["access_token"]
        self.refresh = data.get("refresh_token")
        self.expires_at = time.time() + 12 * 60

    def request(self, method: str, path: str, **kwargs):
        if not self.access or time.time() >= self.expires_at:
            self.login()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access}"
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=15, **kwargs)
        if r.status_code == 401:
            self.login()
            headers["Authorization"] = f"Bearer {self.access}"
            r = requests.request(method, f"{API}{path}", headers=headers, timeout=15, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else {}

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

ft = FT()

def keyboard():
    return ReplyKeyboardMarkup(MENU, resize_keyboard=True, is_persistent=True)

def allowed(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.id == CHAT_ID)

async def send(update: Update, text: str):
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard())

def fmt_pct(v):
    try:
        return f"{float(v) * 100:+.2f}%"
    except Exception:
        return "—"

def fmt_trade(t):
    side = "SHORT 📉" if t.get("is_short") else "LONG 📈"
    pair = t.get("pair", "?")
    lev = t.get("leverage", 1)
    open_rate = t.get("open_rate", 0)
    current = t.get("current_rate", 0)
    profit = fmt_pct(t.get("profit_ratio"))
    stop = t.get("stop_loss_abs")
    tag = t.get("enter_tag") or t.get("buy_tag") or "сигнал стратегии"
    return (
        f"🆔 Сделка #{t.get('trade_id', '?')} · {side}\n"
        f"🪙 {pair}\n"
        f"💵 Вход: {open_rate}\n"
        f"📍 Сейчас: {current}\n"
        f"📊 Результат: {profit}\n"
        f"⚡ Плечо: {lev}x\n"
        f"🛡 Стоп: {stop if stop is not None else 'по правилам стратегии'}\n"
        f"🧠 Причина входа: {tag}"
    )

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    await send(update,
        "🇷🇺 *Prime — главное меню*\n\n"
        "Я перевёл управление на понятные кнопки. Команды вводить не нужно.\n\n"
        "🟢 Запустить — разрешить новые входы.\n"
        "⏸ Пауза — временно запретить новые сделки, открытые продолжат управляться.\n"
        "⛔ Остановить — полностью остановить торговый цикл.\n"
        "📊 Открытые сделки — показать текущие позиции.\n"
        "📈 Результаты — прибыль/убыток и статистика.\n"
        "📜 История — последние закрытые сделки.\n\n"
        "⚠️ Сейчас режим Dry Run: сделки симулируются, реальные деньги не используются."
    )

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update) or not update.message: return
    text = update.message.text
    try:
        if text == "📊 Открытые сделки":
            data = await asyncio.to_thread(ft.get, "/status")
            if not data:
                await send(update, "📊 Открытых сделок сейчас нет.")
            else:
                await send(update, "📊 *Открытые сделки*\n\n" + "\n\n".join(fmt_trade(x) for x in data))
        elif text == "💰 Баланс":
            data = await asyncio.to_thread(ft.get, "/balance")
            rows = data.get("currencies", data if isinstance(data, list) else [])
            out = ["💰 *Баланс*"]
            for x in rows:
                if x.get("currency") == "USDT" or float(x.get("balance", 0) or 0) > 0:
                    out.append(f"• {x.get('currency')}: доступно {x.get('available', 0)}, всего {x.get('balance', 0)}")
            await send(update, "\n".join(out))
        elif text == "📈 Результаты":
            data = await asyncio.to_thread(ft.get, "/profit")
            await send(update,
                "📈 *Результаты стратегии*\n\n"
                f"Закрытых сделок: {data.get('trade_count', 0)}\n"
                f"Средний результат сделки: {fmt_pct(data.get('profit_closed_ratio_mean'))}\n"
                f"Суммарный результат: {fmt_pct(data.get('profit_closed_ratio_sum'))}\n"
                f"Валюта результата: {data.get('stake_currency', 'USDT')}\n\n"
                "ℹ️ Это статистика Dry Run, а не гарантия будущей прибыли.")
        elif text == "📜 История":
            data = await asyncio.to_thread(ft.get, "/trades", params={"limit": 10, "order_by_id": "false"})
            trades = data.get("trades", data if isinstance(data, list) else [])
            if not trades:
                await send(update, "📜 История пока пустая.")
            else:
                out = ["📜 *Последние сделки*"]
                for t in trades[:10]:
                    side = "SHORT" if t.get("is_short") else "LONG"
                    out.append(f"#{t.get('trade_id')} · {t.get('pair')} · {side} · {fmt_pct(t.get('close_profit_abs') or t.get('close_profit'))}")
                await send(update, "\n".join(out))
        elif text == "🟢 Запустить":
            data = await asyncio.to_thread(ft.post, "/start")
            await send(update, "🟢 *Бот запущен.*\nНовые сигналы снова разрешены.\n\n" + str(data.get("status", "")))
        elif text == "⏸ Пауза":
            data = await asyncio.to_thread(ft.post, "/pause")
            await send(update, "⏸ *Новые входы поставлены на паузу.*\nОткрытые сделки продолжают управляться по правилам стратегии.\n\n" + str(data.get("status", "")))
        elif text == "⛔ Остановить":
            data = await asyncio.to_thread(ft.post, "/stop")
            await send(update, "⛔ *Торговый цикл остановлен.*\nОткрытые позиции не удаляются из базы.\n\n" + str(data.get("status", "")))
        elif text == "🔄 Обновить":
            health = await asyncio.to_thread(ft.get, "/health")
            count = await asyncio.to_thread(ft.get, "/count")
            await send(update,
                "🔄 *Состояние обновлено*\n\n"
                f"Состояние: {health.get('status', 'работает')}\n"
                f"Открытых сделок: {count.get('current', 0)} из {count.get('max', 0)}")
        elif text == "ℹ️ Как работает":
            await send(update,
                "ℹ️ *Как работает бот*\n\n"
                "• Бот анализирует ликвидные фьючерсные пары Bybit.\n"
                "• Основной таймфрейм — 5 минут.\n"
                "• Стратегия Prime использует EMA, RSI, ATR, объём и силу тренда.\n"
                "• Есть LONG и SHORT.\n"
                "• Максимальное плечо ограничено 3x.\n"
                "• Мартингейла и усреднения позиции в новой стратегии нет.\n"
                "• Сейчас включён Dry Run — все сделки виртуальные.\n\n"
                "⚠️ Сигнал стратегии не является гарантией прибыли.")
        elif text == "⚙️ Настройки":
            await send(update,
                "⚙️ *Основные настройки*\n\n"
                "Биржа: Bybit\n"
                "Рынок: Futures\n"
                "Маржа: Isolated\n"
                "Таймфрейм: 5m\n"
                "Максимум одновременно: 3 сделки\n"
                "Размер одной сделки: 50 USDT\n"
                "Максимальное плечо: 3x\n"
                "Режим: Dry Run 🧪\n"
                "Мартингейл: ❌")
    except Exception as e:
        await send(update, f"⚠️ Не удалось получить данные. Бот продолжает работать, ошибка интерфейса: {type(e).__name__}")

async def monitor(bot):
    known_open = set()
    known_closed = set()
    initialized = False
    while True:
        try:
            open_trades = await asyncio.to_thread(ft.get, "/status")
            history = await asyncio.to_thread(ft.get, "/trades", params={"limit": 50, "order_by_id": "false"})
            history = history.get("trades", history if isinstance(history, list) else [])
            open_ids = {x.get("trade_id") for x in open_trades}
            closed = [x for x in history if x.get("close_date") and x.get("trade_id") is not None]
            closed_ids = {x.get("trade_id") for x in closed}
            if initialized:
                for t in open_trades:
                    tid = t.get("trade_id")
                    if tid not in known_open:
                        await bot.send_message(CHAT_ID, "🟢 *Новая сделка открыта*\n\n" + fmt_trade(t), parse_mode="Markdown", reply_markup=keyboard())
                for t in closed:
                    tid = t.get("trade_id")
                    if tid not in known_closed:
                        side = "SHORT" if t.get("is_short") else "LONG"
                        await bot.send_message(
                            CHAT_ID,
                            "🔴 *Сделка закрыта*\n\n"
                            f"🪙 {t.get('pair')} · {side}\n"
                            f"💵 Вход: {t.get('open_rate')}\n"
                            f"🏁 Выход: {t.get('close_rate')}\n"
                            f"📊 Результат: {fmt_pct(t.get('close_profit') or t.get('profit_ratio'))}\n"
                            f"📌 Причина: {t.get('exit_reason', 'не указана')}",
                            parse_mode="Markdown",
                            reply_markup=keyboard(),
                        )
            known_open = open_ids
            known_closed = closed_ids
            initialized = True
        except Exception:
            pass
        await asyncio.sleep(10)

async def post_init(app: Application):
    app.create_task(monitor(app.bot))

async def post_shutdown(app: Application):
    await asyncio.sleep(0)

def run_freqtrade():
    return subprocess.Popen([
        "freqtrade", "trade",
        "--config", "/freqtrade/user_data/config.json",
        "--strategy", "PrimeStrategy"
    ])

if __name__ == "__main__":
    child = run_freqtrade()

    def stop_child(*_):
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop_child)
    signal.signal(signal.SIGINT, stop_child)

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        stop_child()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
