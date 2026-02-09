from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return "Bot is running and staying alive!"

def run():
    # Koyeb 會自動分配 PORT，必須監聽 0.0.0.0
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True # 確保主程式結束時執行緒也會關閉
    t.start()
