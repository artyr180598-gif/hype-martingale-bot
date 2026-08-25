#!/bin/bash
# Восстановление окружения после перезапуска песочницы.
# Пакеты ставятся системно и НЕ переживают конец сессии — запускать при необходимости.
set -e
python3 -c "import PIL" 2>/dev/null || pip install -q --break-system-packages pillow
python3 -c "import imageio_ffmpeg" 2>/dev/null || pip install -q --break-system-packages imageio-ffmpeg
echo "ENV OK"
