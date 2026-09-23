FROM freqtradeorg/freqtrade:stable
WORKDIR /freqtrade
COPY user_data /freqtrade/user_data
COPY telegram_ui.py /freqtrade/telegram_ui.py
ENTRYPOINT ["python3"]
CMD ["/freqtrade/telegram_ui.py"]
