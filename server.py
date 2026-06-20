from flask import Flask, jsonify
from threading import Thread
import os

app = Flask(__name__)

# 這是機器人會持續更新的狀態字典
bot_status = {"guild_count": 0, "user_count": 0, "latency": 0}

# 首頁：直接顯示網頁監控面板
@app.route('/', methods=['GET'])
def index():
    return f"""
    <html>
        <head><title>Bot Monitor</title></head>
        <body>
            <h1>機器人即時狀態</h1>
            <p>伺服器總數: {bot_status['guild_count']}</p>
            <p>用戶總數: {bot_status['user_count']}</p>
            <p>延遲: {bot_status['latency']} ms</p>
            <script>setTimeout(function(){{location.reload();}}, 30000);</script>
        </body>
    </html>
    """

# API 接口 (供機器人更新數據用)
@app.route('/api/data', methods=['GET'])
def get_data():
    return jsonify(bot_status)

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
