# 使用官方 Python 映像檔
FROM python:3.11-slim

# 安裝系統層級依賴
# ffmpeg: 處理音訊
# libffi-dev, libnacl-dev: 語音加密必備
# gcc, python3-dev: 編譯套件必備
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    python3-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有程式碼
COPY . .

# 暴露 keep_alive 使用的埠口 (預設 8080)
EXPOSE 8080

# 啟動指令
CMD ["python", "bot.py"]
