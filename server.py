# server.py (完整內容)
from flask import Flask, request, jsonify
from threading import Thread
import os
import psutil
from datetime import datetime

app = Flask(__name__)
_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

ADMIN_PASS = os.environ.get("ADMIN_PASS", "111000")

bot_status = {
    "latency": 0, "cpu": 0, "ram": 0, "logs": [], "broadcast_queue": []
}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

@app.route('/get_channels/<int:guild_id>')
def get_channels(guild_id):
    if not _bot: return jsonify([])
    guild = _bot.get_guild(guild_id)
    channels = [{"id": c.id, "name": c.name} for c in guild.text_channels] if guild else []
    return jsonify(channels)

@app.route('/broadcast', methods=['POST'])
def broadcast():
    if request.form.get('password') != ADMIN_PASS: return "密碼錯誤", 403
    msg = request.form.get('message')
    cid = request.form.get('channel_id')
    if msg and cid:
        bot_status["broadcast_queue"].append({"msg": msg, "cid": int(cid)})
        return "訊息已加入廣播佇列"
    return "錯誤"

@app.route('/', methods=['GET', 'POST'])
def index():
    admin_msg = ""
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASS:
            if request.form.get('action') == "clear_logs": bot_status["logs"] = []
            admin_msg = "操作已執行"
        else: admin_msg = "密碼錯誤"

    # 這裡改用 try-except 確保不會因為讀取時機點錯誤而崩潰
    guild_list = []
    if _bot and hasattr(_bot, 'guilds'):
        guild_list = _bot.guilds
        
    guild_rows = "".join([f"<tr><td>{g.name}</td><td>{g.member_count}</td><td>連線中</td></tr>" for g in guild_list]) if guild_list else "<tr><td colspan='3'>機器人正在連線中...</td></tr>"
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in guild_list]) if guild_list else ""
    
    return f"""
    <html>
        <body style="background: #0f172a; color: white; padding: 20px; font-family: sans-serif;">
            <h1>機器人管理後台</h1>
            <div style="background: #1e293b; padding: 20px; border-radius: 8px;">
                <h3>系統監控</h3>
                <p>CPU: {bot_status['cpu']}% | RAM: {bot_status['ram']}% | 延遲: {bot_status['latency']}ms</p>
                <table width="100%" border="1" style="border-collapse:collapse; margin-top:10px;">
                    <tr><th>伺服器名稱</th><th>成員數</th><th>狀態</th></tr>
                    {guild_rows}
                </table>
                <div id="logs" style="height: 100px; margin-top:10px; overflow-y: scroll; background: #000; padding: 10px; font-family: monospace;">{"<br>".join(bot_status['logs'])}</div>
            </div>
            <div style="margin-top: 20px; display: flex; gap: 20px;">
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>管理操作</h3>
                    <form method="POST"><input type="password" name="password" placeholder="密碼" required><br>
                    <select name="action"><option value="clear_logs">清除日誌</option></select><button type="submit">執行</button></form>
                </div>
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>廣播系統</h3>
                    <form action="/broadcast" method="POST"><input type="password" name="password" placeholder="密碼" required><br>
                    <select id="guild_select" onchange="updateChannels()"><option value="">選擇伺服器</option>{guild_options}</select>
                    <select name="channel_id" id="channel_select"><option value="">請選擇</option></select><br>
                    <textarea name="message" placeholder="輸入訊息" required></textarea><br><button type="submit">發送</button></form>
                </div>
            </div>
            <script>
            function updateChannels() {{
                let gid = document.getElementById('guild_select').value;
                if(!gid) return;
                fetch('/get_channels/' + gid).then(r => r.json()).then(data => {{
                    document.getElementById('channel_select').innerHTML = data.map(c => `<option value="${{c.id}}">${{c.name}}</option>`).join('');
                }});
            }}
            </script>
        </body>
    </html>
    """

def run(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()
