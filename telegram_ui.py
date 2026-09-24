import asyncio,os,time
from telegram import ReplyKeyboardMarkup,Update
from telegram.ext import Application,CommandHandler,ContextTypes,MessageHandler,filters
from signal_engine import scan
TOKEN=os.environ["TELEGRAM_TOKEN"]; CHAT_ID=int(os.environ["TELEGRAM_CHAT_ID"])
MENU=[["🔎 Сканировать","🔥 Лучшие сигналы"],["📚 Как анализируется","⚙️ Настройки"]]
last_sent={}; last_scan={"time":0.0,"signals":[]}; scan_lock=asyncio.Lock()
def keyboard(): return ReplyKeyboardMarkup(MENU,resize_keyboard=True,is_persistent=True)
def allowed(u): return bool(u.effective_chat and u.effective_chat.id==CHAT_ID)
async def send(u,t):
    if u.message: await u.message.reply_text(t,reply_markup=keyboard())
def fmt(s):
    side="LONG 🟢" if s.side=="LONG" else "SHORT 🔴"; rs="\n".join("• "+x for x in s.reasons); ws=("\n\n⚠️ "+"\n".join("• "+x for x in s.warnings)) if s.warnings else ""
    return f"🔥 *{s.symbol} · {side}*\n🧠 Quality Score: *{s.score}/100*\n💵 Сейчас: {s.price:.8g}\n\n🎯 *Зона входа:* {s.entry_low:.8g} — {s.entry_high:.8g}\n🛑 Stop: {s.stop:.8g}\n1️⃣ TP1: {s.tp1:.8g}\n2️⃣ TP2: {s.tp2:.8g}\n3️⃣ TP3: {s.tp3:.8g}\n📐 RR до TP2: *{s.rr:.2f}*\n\n🔬 *Подтверждения:*\n{rs}{ws}\n\nℹ️ Score — сила совпадения факторов, не вероятность."
async def do_scan():
    global last_scan
    async with scan_lock:
        s=await asyncio.to_thread(scan); last_scan={"time":time.time(),"signals":s}; return s
async def start_cmd(u,c):
    if allowed(u): await send(u,"🧠 *Hype Signal Radar*\n\nSIGNAL ONLY — сделки не открывает.\nСейчас используется архитектура live market-intelligence: тренд → структура → ликвидность → OI → recent trade flow → стакан → VWAP → вход.\n\n🔎 ручной скан\n🔥 последние сигналы\nАвтоскан: каждые 5 минут.")
async def menu(u,c):
    if not allowed(u) or not u.message:return
    t=u.message.text
    try:
        if t=="🔎 Сканировать":
            await u.message.reply_text("🔍 Запустил глубокий скан до 80 ликвидных монет. Сначала собираю 5m/15m/1h, затем OI + funding + recent trades + стакан. Это может занять несколько десятков секунд.")
            s=await do_scan()
            if not s: await send(u,"🧊 Сейчас подтверждённого setup нет. Слабый сигнал специально не выдаю.")
            else:
                await send(u,f"🔥 Найдено setups: *{len(s)}*")
                for x in s[:8]: await u.message.reply_text(fmt(x),parse_mode="Markdown",reply_markup=keyboard())
        elif t=="🔥 Лучшие сигналы":
            s=last_scan["signals"]
            if not s: await send(u,"Пока нет результата. Нажми 🔎 Сканировать.")
            else:
                for x in s[:8]: await u.message.reply_text(fmt(x),parse_mode="Markdown",reply_markup=keyboard())
        elif t=="📚 Как анализируется":
            await send(u,"📚 *Архитектура*\n\n• 1h + 15m — режим и bias\n• 5m — trigger\n• подтверждённые swings/BOS\n• liquidity sweep\n• FVG\n• VWAP/ATR/volume\n• OI change\n• funding\n• recent public trades: buy/sell flow\n• top-20 orderbook imbalance\n• анти-погоня\n\nЯ выбрал за основу идею *live market-intelligence* из HyperData Terminal, а research-подход к orderflow — из orderflow-alpha. Код не копируется; используются отдельные проверяемые рыночные данные. HyperData показывает именно OI, CVD/orderflow, liquidations, orderbook и funding как отдельные data-компоненты.")
        elif t=="⚙️ Настройки":
            await send(u,"⚙️ *Signal-only*\nUniverse: до 80 USDT perpetuals\nTF: 5m + 15m + 1h\nДанные: Bybit public API\nМинимальный score: 58\nСделки/ордера: ВЫКЛ\nМартингейл: ВЫКЛ\nПовтор одного setup: не чаще 1 раза/час")
    except Exception as e:
        print(f"scan error: {type(e).__name__}: {e}",flush=True)
        await send(u,"⚠️ Скан завершился ошибкой. Ошибка записана в лог — я не буду делать вид, что всё работает.")
async def monitor(app):
    await asyncio.sleep(15)
    while True:
        try:
            s=await do_scan(); now=time.time()
            for x in s[:8]:
                k=f"{x.symbol}:{x.side}"
                if now-last_sent.get(k,0)>=3600:
                    await app.bot.send_message(CHAT_ID,"🚨 *Новый setup*\n\n"+fmt(x),parse_mode="Markdown",reply_markup=keyboard()); last_sent[k]=now
        except Exception as e: print(f"monitor scan error: {type(e).__name__}: {e}",flush=True)
        await asyncio.sleep(300)
async def post_init(app): app.bot_data["monitor_task"]=asyncio.create_task(monitor(app)); print("Hype Signal Radar: SIGNAL ONLY",flush=True)
async def post_shutdown(app):
    t=app.bot_data.get("monitor_task")
    if t:t.cancel()
def main():
    app=Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start",start_cmd)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,menu)); app.run_polling(drop_pending_updates=True)
if __name__=="__main__": main()
