from flask import Flask, request, jsonify
from threading import Thread
import os
import psutil
from datetime import datetime

# 1. 初始化 Flask
app = Flask(__name__)
_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

# 環境變數設定
ADMIN_PASS = os.environ.get("ADMIN_PASS", "111000")

# 狀態儲存
bot_status = {
    "latency": 0, "cpu": 0, "ram": 0, "logs": [], "broadcast_queue": []
}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

# 路由：取得頻道清單 (給前端 AJAX 用)
@app.route('/get_channels/<int:guild_id>')
def get_channels(guild_id):
    if not _bot: return jsonify([])
    guild = _bot.get_guild(guild_id)
    channels = [{"id": c.id, "name": c.name} for c in guild.text_channels] if guild else []
    return jsonify(channels)

# 路由：處理廣播
@app.route('/broadcast', methods=['POST'])
def broadcast():
    if request.form.get('password') != ADMIN_PASS: return "密碼錯誤", 403
    msg = request.form.get('message')
    cid = request.form.get('channel_id')
    if msg and cid:
        bot_status["broadcast_queue"].append({"msg": msg, "cid": int(cid)})
        return "訊息已加入廣播佇列，機器人將於 5 秒內發送。"
    return "錯誤：資料不完整"

# 路由：主頁面
@app.route('/', methods=['GET', 'POST'])
def index():
    admin_msg = ""
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASS:
            if request.form.get('action') == "clear_logs": 
                bot_status["logs"] = []
                admin_msg = "日誌已清除"
        else: 
            admin_msg = "密碼錯誤"

    # 生成表格與選單
    guild_rows = "".join([f"<tr><td>{g.name}</td><td>{g.member_count}</td><td>連線中</td></tr>" for g in _bot.guilds]) if _bot else "<tr><td colspan='3'>機器人尚未啟動</td></tr>"
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in _bot.guilds]) if _bot else ""
    
    return f"""
    <html>
        <head><meta charset="utf-8"><title>機器人管理後台</title></head>
        <body style="background: #0f172a; color: white; padding: 20px; font-family: sans-serif;">
            <h1>機器人管理後台</h1>
            <div style="background: #1e293b; padding: 20px; border-radius: 8px;">
                <h3>系統監控</h3>
                <p>CPU: {bot_status['cpu']}% | RAM: {bot_status['ram']}% | 延遲: {bot_status['latency']}ms</p>
                <table width="100%" border="1" style="border-collapse:collapse; margin-top:10px; color:white;">
                    <tr><th>伺服器名稱</th><th>成員數</th><th>狀態</th></tr>
                    {guild_rows}
                </table>
                <div id="logs" style="height: 100px; margin-top:10px; overflow-y: scroll; background: #000; padding: 10px; font-family: monospace;">{"<br>".join(bot_status['logs'])}</div>
            </div>
            
            <div style="margin-top: 20px; display: flex; gap: 20px;">
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>管理操作</h3>
                    <form method="POST">
                        <input type="password" name="password" placeholder="管理密碼" required><br>
                        <select name="action"><option value="clear_logs">清除日誌</option></select>
                        <button type="submit">執行</button>
                    </form>
                </div>
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>廣播系統</h3>
                    <form action="/broadcast" method="POST">
                        <input type="password" name="password" placeholder="管理密碼" required><br>
                        <select id="guild_select" onchange="updateChannels()">
                            <option value="">選擇伺服器</option>{guild_options}
                        </select>
                        <select name="channel_id" id="channel_select">
                            <option value="">請先選擇伺服器</option>
                        </select><br>
                        <textarea name="message" placeholder="輸入訊息" required></textarea><br>
                        <button type="submit">發送廣播</button>
                    </form>
                </div>
            </div>
            <script>
            function updateChannels() {{
                let gid = document.getElementById('guild_select').value;
                fetch('/get_channels/' + gid).then(r => r.json()).then(data => {{
                    document.getElementById('channel_select').innerHTML = data.map(c => `<option value="${{c.id}}">${{c.name}}</option>`).join('');
                }});
            }}
            </script>
        </body>
    </html>
    """

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run)
    t.start()
