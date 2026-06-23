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
    "latency": 0, "cpu": 0, "ram": 0, "logs": [], "broadcast_queue": [], "restart_requested": False
}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

@app.route('/get_channels/<int:guild_id>')
def get_channels(guild_id):
    if not _bot or not _bot.is_ready(): return jsonify([])
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
            action = request.form.get('action')
            if action == "clear_logs": 
                bot_status["logs"] = []
                admin_msg = "日誌已清除"
            elif action == "restart":
                bot_status["restart_requested"] = True
                admin_msg = "已發送重啟指令，機器人將於數秒後重啟"
        else: admin_msg = "密碼錯誤"

    guild_list = _bot.guilds if _bot and _bot.is_ready() else []
    guild_rows = "".join([f"<tr><td>{g.name}</td><td>{g.member_count}</td><td>連線中</td></tr>" for g in guild_list]) if guild_list else "<tr><td colspan='3'>機器人正在啟動中...</td></tr>"
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in guild_list]) if guild_list else ""
    
    return f"""
    <html>
        <body style="background: #0f172a; color: white; padding: 20px; font-family: sans-serif;">
            <h1>機器人管理後台</h1>
            <div id="status_msg" style="color: #4ade80;"></div>
            <div style="margin-top: 20px; display: flex; gap: 20px;">
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>管理操作</h3>
                    <button onclick="sendAction('clear_logs')">清除日誌</button>
                    <button onclick="sendAction('restart')">重啟機器人</button>
                </div>
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>廣播系統</h3>
                    <input type="password" id="pass" placeholder="密碼"><br>
                    <select id="guild_select" onchange="updateChannels()"><option value="">選擇伺服器</option>{guild_options}</select>
                    <select id="channel_select"><option value="">請選擇頻道</option></select><br>
                    <textarea id="msg" placeholder="輸入訊息"></textarea><br>
                    <button onclick="sendBroadcast()">發送廣播</button>
                </div>
            </div>

            <script>
            function updateChannels() {{
                let gid = document.getElementById('guild_select').value;
                fetch('/get_channels/' + gid).then(r => r.json()).then(data => {{
                    document.getElementById('channel_select').innerHTML = data.map(c => `<option value="${{c.id}}">${{c.name}}</option>`).join('');
                }});
            }}
            
            function sendBroadcast() {{
                let fd = new FormData();
                fd.append('password', document.getElementById('pass').value);
                fd.append('message', document.getElementById('msg').value);
                fd.append('channel_id', document.getElementById('channel_select').value);
                fetch('/broadcast', {{method: 'POST', body: fd}}).then(r => r.text()).then(t => alert(t));
            }}

            function sendAction(act) {{
                let fd = new FormData();
                fd.append('password', document.getElementById('pass').value);
                fd.append('action', act);
                fetch('/', {{method: 'POST', body: fd}}).then(() => location.reload());
            }}
            </script>
        </body>
    </html>

    """

def run(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()
