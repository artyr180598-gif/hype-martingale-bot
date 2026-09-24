import asyncio
import os
import time

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from signal_engine import scan

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

MENU = [["🔎 Сканировать", "🔥 Лучшие сигналы"], ["📚 Как анализируется", "⚙️ Настройки"]]
last_sent = {}
last_scan = {"time": 0.0, "signals": []}
scan_lock = asyncio.Lock()

def keyboard():
    return ReplyKeyboardMarkup(MENU, resize_keyboard=True, is_persistent=True)

def allowed(update):
    return bool(update.effective_chat and update.effective_chat.id == CHAT_ID)

async def send(update, text):
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard())

def fmt(s):
    side = "LONG 🟢" if s.side == "LONG" else "SHORT 🔴"
    reasons = "\n".join("• " + x for x in s.reasons)
    warnings = ("\n\n⚠️ " + "\n".join("• " + x for x in s.warnings)) if s.warnings else ""
    return (
        f"🔥 *{s.symbol} · {side}*\n"
        f"🧠 Quality Score: *{s.score}/100*\n"
        f"💵 Сейчас: {s.price:.8g}\n\n"
        f"🎯 *Зона входа:* {s.entry_low:.8g} — {s.entry_high:.8g}\n"
        f"🛑 Stop: {s.stop:.8g}\n"
        f"1️⃣ TP1: {s.tp1:.8g}\n"
        f"2️⃣ TP2: {s.tp2:.8g}\n"
        f"3️⃣ TP3: {s.tp3:.8g}\n"
        f"📐 RR до TP2: *{s.rr:.2f}*\n\n"
        f"🔬 *Почему:*\n{reasons}{warnings}\n\n"
        "ℹ️ Score — сила совпадения факторов, не вероятность выигрыша."
    )

async def do_scan():
    global last_scan
    async with scan_lock:
        signals = await asyncio.to_thread(scan)
        last_scan = {"time": time.time(), "signals": signals}
        return signals

async def start_cmd(update, context):
    if not allowed(update):
        return
    await send(update,
        "🧠 *Hype Signal Radar*\n\n"
        "Это *не торговый бот*. Он не открывает сделки и не управляет депозитом.\n\n"
        "Он сканирует ликвидные Bybit USDT-перпетуалы и ищет редкие, многослойные точки входа.\n\n"
        "🔎 Сканировать — полный анализ сейчас.\n"
        "🔥 Лучшие сигналы — последние найденные setups.\n"
        "Автоматический скан запускается каждые 5 минут."
    )

async def menu_handler(update, context):
    if not allowed(update) or not update.message:
        return
    text = update.message.text
    try:
        if text == "🔎 Сканировать":
            await update.message.reply_text("🔍 Сканирую: 5m + 15m + 1h + структура + ликвидность + объём + VWAP + стакан…")
            signals = await do_scan()
            if not signals:
                await send(update, "🧊 Сильного setup сейчас не найдено. Я не буду выдавать слабый сигнал ради количества.")
            else:
                await send(update, f"🔥 Найдено сильных setups: *{len(signals)}*")
                for s in signals[:5]:
                    await update.message.reply_text(fmt(s), parse_mode="Markdown", reply_markup=keyboard())
        elif text == "🔥 Лучшие сигналы":
            signals = last_scan["signals"]
            if not signals:
                await send(update, "Пока нет свежего результата. Нажми 🔎 Сканировать.")
            else:
                for s in signals[:5]:
                    await update.message.reply_text(fmt(s), parse_mode="Markdown", reply_markup=keyboard())
        elif text == "📚 Как анализируется":
            await send(update,
                "📚 *Логика Signal Radar*\n\n"
                "1. Рынок: ликвидные USDT perpetuals, сначала отсев по обороту.\n"
                "2. HTF: 1h задаёт контекст, 15m подтверждает направление, 5m ищет вход.\n"
                "3. Структура: подтверждённые swing high/low, BOS и переломы структуры.\n"
                "4. Ликвидность: sweep предыдущих экстремумов с возвратом цены.\n"
                "5. Зоны: FVG и order-block proxy.\n"
                "6. Flow: относительный объём + направление свечи.\n"
                "7. VWAP: положение цены относительно объёмной справедливой цены.\n"
                "8. Стакан: top-20 imbalance как дополнительное подтверждение.\n"
                "9. Анти-погоня: слишком растянутые движения отбрасываются.\n"
                "10. Уровни: stop за структурой, TP строятся от риска.\n\n"
                "Архитектурные идеи сверялись с Jesse, SMC-проектами, order-book/CVD research и Bybit market-data tooling. Код этих проектов не копировался."
            )
        elif text == "⚙️ Настройки":
            await send(update,
                "⚙️ *Текущие настройки*\n\n"
                "Режим: SIGNAL ONLY\n"
                "Исполнение сделок: ВЫКЛЮЧЕНО\n"
                "Источник: Bybit public market data\n"
                "Universe: до 50 самых ликвидных USDT perpetuals\n"
                "Основной TF: 5m\n"
                "Подтверждение: 15m + 1h\n"
                "Минимальный Quality Score: 78/100\n"
                "Максимум отправляемых сигналов за цикл: 5\n"
                "Плечо и мартингейл: отсутствуют."
            )
    except Exception:
        await send(update, "⚠️ Скан временно не завершился. Следующий цикл попробует снова.")

async def monitor(app):
    await asyncio.sleep(15)
    while True:
        try:
            signals = await do_scan()
            now = time.time()
            for s in signals[:5]:
                key = f"{s.symbol}:{s.side}"
                if now - last_sent.get(key, 0) >= 3600:
                    await app.bot.send_message(CHAT_ID, "🚨 *Новый сильный setup*\n\n" + fmt(s),
                                               parse_mode="Markdown", reply_markup=keyboard())
                    last_sent[key] = now
        except Exception:
            pass
        await asyncio.sleep(300)

async def post_init(app):
    app.bot_data["monitor_task"] = asyncio.create_task(monitor(app))
    print("Hype Signal Radar started: SIGNAL ONLY", flush=True)

async def post_shutdown(app):
    task = app.bot_data.get("monitor_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
