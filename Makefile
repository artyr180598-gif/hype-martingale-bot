.PHONY: help install test lint check run serve scan signal backtest walkforward calibrate watch bot status clean

help:
	@echo "HYPE Futures Signal Intelligence — единый движок v3"
	@echo ""
	@echo "  make install       - установить зависимости"
	@echo "  make test          - запустить тесты (v3)"
	@echo "  make lint          - ruff"
	@echo "  make check         - lint + тесты"
	@echo "  make run           - API + watcher + Telegram в одном процессе (daemon)"
	@echo "  make serve         - только FastAPI"
	@echo "  make scan          - скан вселенной USDT-perp"
	@echo "  make signal SYM=BTCUSDT MODE=pro - разовый сигнал"
	@echo "  make backtest SYM=BTCUSDT TF=15m BARS=2000"
	@echo "  make walkforward SYM=BTCUSDT TF=15m BARS=5000 FOLDS=5"
	@echo "  make calibrate SYMS=BTCUSDT,ETHUSDT TF=15m BARS=2000"
	@echo "  make watch SYMS=BTCUSDT,ETHUSDT - фоновый lifecycle-наблюдатель"
	@echo "  make bot           - Telegram-бот + watcher"
	@echo "  make status        - сохранённые сигналы и health"
	@echo "  make clean         - очистить кэши"

install:
	pip install -r requirements.txt

test:
	pytest -q

lint:
	ruff check .

check: lint test

run:
	python -m v3 daemon --host $(or $(HOST),0.0.0.0) --port $(or $(PORT),8400)

serve:
	python -m v3 serve --host $(or $(HOST),0.0.0.0) --port $(or $(PORT),8400)

scan:
	python -m v3 scan --mode $(or $(MODE),beginner) --limit $(or $(LIMIT),250) --top $(or $(TOP),20)

signal:
	python -m v3 signal $(or $(SYM),BTCUSDT) --mode $(or $(MODE),beginner)

backtest:
	python -m v3 backtest $(or $(SYM),BTCUSDT) --tf $(or $(TF),15m) --bars $(or $(BARS),1000) --warmup $(or $(WARMUP),120)

walkforward:
	python -m v3 walkforward $(or $(SYM),BTCUSDT) --tf $(or $(TF),15m) --bars $(or $(BARS),5000) --folds $(or $(FOLDS),5)

calibrate:
	python -m v3 calibrate $(or $(SYMS),BTCUSDT,ETHUSDT,SOLUSDT) --tf $(or $(TF),15m) --bars $(or $(BARS),2000) --warmup $(or $(WARMUP),120)

watch:
	python -m v3 watch $(or $(SYMS),BTCUSDT,ETHUSDT)

bot:
	python -m v3 bot

status:
	python -m v3 status

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
