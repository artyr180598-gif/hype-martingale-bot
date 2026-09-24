import asyncio
import logging
import os
import signal
from dotenv import load_dotenv
from src.data_layer.hub import HyperDataHub
from src.telegram_bot import TelegramBot

load_dotenv()

# Railway worker entrypoint; application logic unchanged.

async def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    hub = HyperDataHub(demo=False)
    bot = TelegramBot(hub)
    await hub.start()
    try:
        await bot.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        await stop.wait()
    finally:
        await bot.stop()
        await hub.stop()

if __name__ == '__main__':
    asyncio.run(main())
