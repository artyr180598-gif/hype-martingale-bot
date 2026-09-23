FROM freqtradeorg/freqtrade:stable
WORKDIR /freqtrade
COPY user_data /freqtrade/user_data
COPY telegram_ui.py /freqtrade/telegram_ui.py
CMD ["python", "/freqtrade/telegram_ui.py"]
