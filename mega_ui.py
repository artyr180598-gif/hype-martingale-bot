import asyncio,os,time
from telegram import ReplyKeyboardMarkup
from telegram.ext import Application,CommandHandler,MessageHandler,filters
from signal_engine import scan
TOKEN=os.environ["TELEGRAM_TOKEN"]; CHAT_ID=int(os.environ["TELEGRAM_CHAT_ID"])
MENU=[["🔎 Сканировать","🔥 Лучшие сигналы"],["📊 Статус","📚 Анализ"]]
lock=asyncio.Lock(); last={"time":0.0,"signals":[]}; sent={}
def kb(): return ReplyKeyboardMarkup(MENU,resize_keyboard=True,is_persistent=True)
def allowed(u): return bool(u.effective_chat and u.effective_chat.id==CHAT_ID)
def fmt(s):
    side="LONG 🟢" if s.side=="LONG" else "SHORT 🔴"; rs="\n".join("• "+x for x in s.reasons)
    ws=("\n\n⚠️ "+"\n".join("• "+x for x in s.warnings)) if s.warnings else ""
    return f"🔥 *{s.symbol} · {side}*\n🧠 Quality: *{s.score}/100*\n💵 Price: {s.price:.8g}\n\n🎯 Entry: {s.entry_low:.8g} — {s.entry_high:.8g}\n🛑 Stop: {s.stop:.8g}\n1️⃣ TP1: {s.tp1:.8g}\n2️⃣ TP2: {s.tp2:.8g}\n3️⃣ TP3: {s.tp3:.8g}\n📐 RR≈{s.rr:.2f}\n\n🔬 *Why:*\n{rs}{ws}\n\nℹ️ Score = factor alignment, not probability. SIGNAL ONLY — no orders."
async def run_scan():
    async with lock:
        s=await asyncio.to_thread(scan); last["time"]=time.time(); last["signals"]=s; return s,dict(scan.last_stats)
async def send(u,text):
    if u.message: await u.message.reply_text(text,reply_markup=kb())
async def start(u,c):
    if allowed(u): await send(u,"🧠 *Hype Mega Signal Radar 5*\n\nSignal-only. Никаких ордеров и мартингейла.\nЯдро: multi-venue market intelligence + multi-timeframe structure + liquidity + OI + orderbook + trade-flow.\n\n🔎 Сканировать\n🔥 Лучшие сигналы\n📊 Статус")
async def menu(u,c):
    if not allowed(u) or not u.message:return
    t=u.message.text
    try:
        if t=="🔎 Сканировать":
            await u.message.reply_text("🔍 Mega Scan: universe → 5m/15m/1h → OI/funding → orderbook → trade flow → Binance/OKX confirmation.")
            s,st=await run_scan()
            if not s: await send(u,f"🧊 Подтверждённого setup нет.\nUniverse: {st['universe']}\nОшибки: {st['failed']}\nВремя: {st['seconds']} c")
            else:
                await send(u,f"🔥 Найдено: *{len(s)}*\nUniverse: {st['universe']} · errors: {st['failed']} · {st['seconds']} c")
                for x in s[:8]: await u.message.reply_text(fmt(x),parse_mode="Markdown",reply_markup=kb())
        elif t=="🔥 Лучшие сигналы":
            s=last["signals"]
            if not s: await send(u,"Пока нет результата. Нажми 🔎 Сканировать.")
            else:
                for x in s[:8]: await u.message.reply_text(fmt(x),parse_mode="Markdown",reply_markup=kb())
        elif t=="📊 Статус":
            st=scan.last_stats; await send(u,f"📊 *Scanner status*\nUniverse: {st['universe']}\nErrors: {st['failed']}\nSignals: {st['signals']}\nLast scan: {st['seconds']} sec\nMode: signal-only\nOrders: OFF")
        elif t=="📚 Анализ":
            await send(u,"📚 *Mega architecture*\n\n1h + 15m regime\n5m trigger\nBOS / swings / liquidity sweeps / FVG\nVWAP / ATR / relative volume\nOI + funding\nTop-50 orderbook\nRecent public trade flow\nBinance + OKX confirmation\nAnti-chase\n\nИсточники не подменяются: недоступный компонент помечается warning.")
    except Exception as e:
        print(f"UI error: {type(e).__name__}: {e}",flush=True); await send(u,f"⚠️ Ошибка: {type(e).__name__}: {e}")
async def monitor(app):
    await asyncio.sleep(10)
    while True:
        try:
            s,_=await run_scan(); now=time.time()
            for x in s[:5]:
                k=f"{x.symbol}:{x.side}"
                if now-sent.get(k,0)>=3600:
                    await app.bot.send_message(CHAT_ID,"🚨 *NEW SETUP*\n\n"+fmt(x),parse_mode="Markdown",reply_markup=kb()); sent[k]=now
        except Exception as e: print(f"monitor error: {type(e).__name__}: {e}",flush=True)
        await asyncio.sleep(300)
async def post_init(app):
    app.bot_data["task"]=asyncio.create_task(monitor(app)); print("Hype Mega Signal Radar 5 started — signal-only",flush=True)
async def post_shutdown(app):
    t=app.bot_data.get("task")
    if t:t.cancel()
def main():
    app=Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,menu)); app.run_polling(drop_pending_updates=True)
if __name__=="__main__": main()
