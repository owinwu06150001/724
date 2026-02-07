from flask import Flask
from threading import Thread
import os

app = Flask("")

@app.route("/")
def home():
    return "Bot is running 24/7"

def run():
    # 這裡使用 Render 要求的 Port
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()