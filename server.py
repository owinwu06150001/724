from flask import Flask, jsonify, request
from threading import Thread
import os
import psutil

app = Flask(__name__)

# 狀態儲存
bot_status = {
    "guild_count": 0, "user_count": 0, "latency": 0,
    "guilds": [], "cpu": 0, "ram": 0
}

@app.route('/', methods=['GET'])
def index():
    guild_rows = "".join([f"<tr><td>{g['name']}</td><td>{g['members']}</td><td>{g['status']}</td></tr>" for g in bot_status['guilds']])
    return f"""
    <html>
        <head>
            <meta charset="UTF-8">
            <title>Bot Control Panel</title>
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #0f172a; color: white; padding: 20px; }}
                .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }}
                .card {{ background: #1e293b; padding: 20px; border-radius: 12px; }}
                table {{ width: 100%; border-collapse: collapse; background: #1e293b; margin-top: 20px; }}
                th, td {{ padding: 12px; border-bottom: 1px solid #334155; text-align: left; }}
            </style>
        </head>
        <body>
            <h1>機器人監控中心</h1>
            <div class="grid">
                <div class="card"><h3>CPU 使用率</h3><p>{bot_status['cpu']}%</p></div>
                <div class="card"><h3>記憶體使用</h3><p>{bot_status['ram']}%</p></div>
                <div class="card"><h3>延遲</h3><p>{bot_status['latency']} ms</p></div>
            </div>
            <table>
                <tr><th>伺服器名稱</th><th>人數</th><th>狀態</th></tr>
                {guild_rows}
            </table>
            <script>setTimeout(()=>location.reload(), 10000);</script>
        </body>
    </html>
    """

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run)
    t.start()
