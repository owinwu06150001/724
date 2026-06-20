from flask import Flask, request, jsonify
from threading import Thread
import os
import psutil
from datetime import datetime

app = Flask(__name__)

# 讀取環境變數中的密碼，若未設定則預設為 123456
ADMIN_PASS = os.environ.get("ADMIN_PASS", "123456")

# 狀態儲存
bot_status = {
    "guild_count": 0, "user_count": 0, "latency": 0,
    "guilds": [], "cpu": 0, "ram": 0, "logs": []
}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

@app.route('/', methods=['GET', 'POST'])
def index():
    admin_msg = ""
    # 處理管理員動作
    if request.method == 'POST':
        password = request.form.get('password')
        action = request.form.get('action')
        
        if password == ADMIN_PASS:
            if action == "clear_logs":
                bot_status["logs"] = []
                admin_msg = "已清除日誌"
            elif action == "restart":
                admin_msg = "系統已發送重啟請求"
                # 在此處可以加入呼叫 bot 重啟的邏輯
        else:
            admin_msg = "密碼錯誤，拒絕執行！"

    # 生成 HTML
    guild_rows = "".join([f"<tr><td>{g['name']}</td><td>{g['members']}</td><td>{g['status']}</td></tr>" for g in bot_status['guilds']])
    logs_display = "<br>".join(bot_status['logs'])
    
    return f"""
    <html>
        <head><meta charset="UTF-8"><title>Bot Control Panel</title></head>
        <body style="font-family: sans-serif; background: #0f172a; color: white; padding: 20px;">
            <h1>機器人監控中心</h1>
            <div style="background: #1e293b; padding: 20px; border-radius: 8px;">
                <h3>系統狀況</h3>
                <p>CPU: {bot_status['cpu']}% | RAM: {bot_status['ram']}% | 延遲: {bot_status['latency']}ms</p>
                <div style="height: 150px; overflow-y: scroll; background: #000; padding: 10px; font-family: monospace;">{logs_display}</div>
                <table width="100%" border="1" style="border-collapse:collapse; margin-top:10px;">
                    <tr><th>伺服器</th><th>成員</th><th>狀態</th></tr>
                    {guild_rows}
                </table>
            </div>
            <div style="background: #334155; padding: 20px; border-radius: 8px; margin-top: 20px;">
                <h3>管理員專區 (需密碼)</h3>
                <form method="POST">
                    <input type="password" name="password" placeholder="管理密碼" required>
                    <select name="action">
                        <option value="clear_logs">清除日誌</option>
                        <option value="restart">重啟服務</option>
                    </select>
                    <button type="submit">執行</button>
                </form>
                <p style="color: yellow;">{admin_msg}</p>
            </div>
        </body>
    </html>
    """

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run)
    t.start()
