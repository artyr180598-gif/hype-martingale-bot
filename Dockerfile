FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY signal_engine.py mega_ui.py ./
CMD ["python", "mega_ui.py"]
