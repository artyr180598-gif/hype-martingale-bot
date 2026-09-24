FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY signal_engine.py telegram_ui.py ./
CMD ["python", "telegram_ui.py"]
