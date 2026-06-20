from flask import Flask, jsonify
from threading import Thread
import os

app = Flask(__name__)

# 狀態字典：儲存全域統計與詳細列表
bot_status = {
    "guild_count": 0, 
    "user_count": 0, 
    "latency": 0,
    "guilds": [] # 儲存每個伺服器的詳細資訊
}

@app.route('/', methods=['GET'])
def index():
    # 產生伺服器列表 HTML
    guild_rows = ""
    for g in bot_status['guilds']:
        guild_rows += f"<tr><td>{g['name']}</td><td>{g['members']}</td><td>{g['status']}</td></tr>"
    
    return f"""
    <html>
        <head><style>
            body {{ font-family: sans-serif; padding: 20px; background: #f4f4f9; }}
            table {{ width: 100%; border-collapse: collapse; background: white; }}
            th, td {{ padding: 10px; border: 1px solid #ddd; text-align: left; }}
            .card {{ background: white; padding: 15px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
        </style></head>
        <body>
            <h1>機器人監控中心</h1>
            <div class="card">
                <p>伺服器總數: {bot_status['guild_count']} | 用戶總數: {bot_status['user_count']} | 延遲: {bot_status['latency']} ms</p>
            </div>
            <table>
                <tr><th>伺服器名稱</th><th>成員數</th><th>狀態</th></tr>
                {guild_rows}
            </table>
            <script>setTimeout(function(){{location.reload();}}, 30000);</script>
        </body>
    </html>
    """

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run)
    t.start()
