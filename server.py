from flask import Flask, request, jsonify
from threading import Thread
import os
import psutil
from datetime import datetime

app = Flask(__name__)

ADMIN_PASS = os.environ.get("ADMIN_PASS", "000011")

bot_status = {
    "guild_count": 0, "user_count": 0, "latency": 0,
    "guilds": [], "cpu": 0, "ram": 0, "logs": [],
    "broadcast_queue": [] # 新增：廣播佇列
}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

@app.route('/broadcast', methods=['POST'])
def broadcast():
    password = request.form.get('password')
    if password != ADMIN_PASS:
        return "密碼錯誤 拒絕執行！", 403
    
    msg = request.form.get('message')
    channel_id = request.form.get('channel_id')
    if msg and channel_id:
        try:
            bot_status["broadcast_queue"].append({"msg": msg, "cid": int(channel_id)})
            return "訊息已排入廣播佇列 機器人將於 5 秒內發送"
        except ValueError:
            return "頻道 ID 必須為數字"
    return "請輸入訊息與頻道 ID"

@app.route('/', methods=['GET', 'POST'])
def index():
    admin_msg = ""
    if request.method == 'POST':
        password = request.form.get('password')
        action = request.form.get('action')
        if password == ADMIN_PASS:
            if action == "clear_logs":
                bot_status["logs"] = []
                admin_msg = "日誌已清除"
            elif action == "restart":
                admin_msg = "重啟指令已觸發"
        else:
            admin_msg = "密碼錯誤"

    guild_rows = "".join([f"<tr><td>{g['name']}</td><td>{g['members']}</td><td>{g['status']}</td></tr>" for g in bot_status['guilds']])
    logs_display = "<br>".join(bot_status['logs'])
    
    return f"""
    <html>
        <body style="background: #0f172a; color: white; padding: 20px; font-family: sans-serif;">
            <h1>機器人控制台</h1>
            <div style="background: #1e293b; padding: 20px; border-radius: 8px;">
                <h3>系統監控</h3>
                <p>CPU: {bot_status['cpu']}% | RAM: {bot_status['ram']}% | 延遲: {bot_status['latency']}ms</p>
                <div style="height: 150px; overflow-y: scroll; background: #000; padding: 10px; font-family: monospace;">{logs_display}</div>
            </div>
            
            <div style="margin-top: 20px; display: flex; gap: 20px;">
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>管理員操作</h3>
                    <form method="POST">
                        <input type="password" name="password" placeholder="密碼" required><br>
                        <select name="action"><option value="clear_logs">清除日誌</option><option value="restart">重啟服務</option></select>
                        <button type="submit">執行</button>
                    </form>
                </div>
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>訊息廣播</h3>
                    <form action="/broadcast" method="POST">
                        <input type="password" name="password" placeholder="密碼" required><br>
                        <input type="text" name="channel_id" placeholder="頻道 ID" required><br>
                        <textarea name="message" placeholder="輸入訊息" required></textarea><br>
                        <button type="submit">廣播發送</button>
                    </form>
                </div>
            </div>
            <p style="color: yellow;">{admin_msg}</p>
        </body>
    </html>
    """

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run)
    t.start()
