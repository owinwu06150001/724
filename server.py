from flask import Flask, jsonify
from threading import Thread
import os
import psutil
from datetime import datetime

app = Flask(__name__)

# 擴展後的狀態字典
bot_status = {
    "guild_count": 0, "user_count": 0, "latency": 0,
    "guilds": [], "cpu": 0, "ram": 0, "logs": []
}

# 輔助函式：新增日誌
def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

@app.route('/', methods=['GET'])
def index():
    guild_rows = "".join([f"<tr><td>{g['name']}</td><td>{g['members']}</td><td>{g['status']}</td></tr>" for g in bot_status['guilds']])
    logs_display = "<br>".join(bot_status['logs'])
    
    return f"""
    <html>
        <head>
            <meta charset="UTF-8">
            <title>Bot Dashboard</title>
            <style>
                body {{ font-family: sans-serif; background: #0f172a; color: white; padding: 20px; }}
                .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
                .card {{ background: #1e293b; padding: 15px; border-radius: 8px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; background: #1e293b; }}
                th, td {{ padding: 12px; border: 1px solid #334155; text-align: left; }}
                .log-box {{ height: 200px; overflow-y: scroll; background: #000; padding: 10px; font-family: monospace; border-radius: 5px; margin-top: 10px; }}
            </style>
        </head>
        <body>
            <h1>機器人監控</h1>
            <div class="grid">
                <div class="card"><h3>CPU 使用率</h3><p>{bot_status['cpu']}%</p></div>
                <div class="card"><h3>RAM 使用</h3><p>{bot_status['ram']}%</p></div>
                <div class="card"><h3>Discord 延遲</h3><p>{bot_status['latency']} ms</p></div>
            </div>
            <div class="card" style="margin-top:20px;">
                <h3>即時日誌 (Logs)</h3>
                <div class="log-box">{logs_display}</div>
            </div>
            <table>
                <tr><th>伺服器名稱</th><th>成員數</th><th>狀態</th></tr>
                {guild_rows}
            </table>
            <script>setTimeout(()=>location.reload(), 30000);</script>
        </body>
    </html>
    """

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run)
    t.start()
