from flask import Flask, request, jsonify
from threading import Thread
import os, psutil
from datetime import datetime

app = Flask(__name__)
_bot = None
broadcast_queue = [] 

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

ADMIN_PASS = os.environ.get("ADMIN_PASS", "111000")
bot_status = {"latency": 0, "cpu": 0, "ram": 0, "logs": [], "restart_requested": False}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

@app.route('/get_channels/<int:guild_id>')
def get_channels(guild_id):
    if not _bot or not _bot.is_ready(): return jsonify([])
    guild = _bot.get_guild(guild_id)
    return jsonify([{"id": c.id, "name": c.name} for c in guild.text_channels]) if guild else jsonify([])

@app.route('/broadcast', methods=['POST'])
def broadcast():
    data = request.json
    if data.get('password') != ADMIN_PASS: return jsonify({"status": "error", "msg": "密碼錯誤"})
    broadcast_queue.append({"msg": data.get('message'), "cid": int(data.get('channel_id'))})
    return jsonify({"status": "success", "msg": "訊息已加入佇列"})

@app.route('/', methods=['GET', 'POST'])
def index():
    # 處理管理動作 (重啟/清空日誌)
    if request.method == 'POST':
        data = request.json
        if data.get('password') == ADMIN_PASS:
            if data.get('action') == "clear_logs": bot_status["logs"] = []
            elif data.get('action') == "restart": bot_status["restart_requested"] = True
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "msg": "密碼錯誤"})

    guild_list = _bot.guilds if _bot and _bot.is_ready() else []
    guild_rows = "".join([f"<tr><td>{g.name}</td><td>{g.member_count}</td><td>連線中</td></tr>" for g in guild_list])
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in guild_list])
    
    return f"""
    <html>
    <body style="background:#0f172a; color:white; padding:20px; font-family:sans-serif;">
        <h1>機器人管理後台</h1>
        <div style="background:#1e293b; padding:15px; border-radius:8px;">
            <h3>系統監控</h3>
            <p>CPU: {bot_status['cpu']}% | RAM: {bot_status['ram']}% | 延遲: {bot_status['latency']}ms</p>
            <table width="100%" border="1" style="border-collapse:collapse;">
                <tr><th>伺服器</th><th>成員</th><th>狀態</th></tr>{guild_rows}
            </table>
            <div id="logs" style="height:100px; overflow-y:scroll; background:#000; margin-top:10px; padding:5px; font-family:monospace;">
                {"<br>".join(bot_status['logs'])}
            </div>
        </div>
        <div style="margin-top:20px; display:flex; gap:20px;">
            <div>
                <h3>管理</h3>
                <input type="password" id="admin_pass" placeholder="密碼">
                <button onclick="act('clear_logs')">清除日誌</button>
                <button onclick="act('restart')">重啟機器人</button>
            </div>
            <div>
                <h3>廣播系統</h3>
                <select id="g_sel" onchange="upd()"><option value="">選擇伺服器</option>{guild_options}</select>
                <select id="c_sel"><option value="">選擇頻道</option></select><br>
                <textarea id="msg" placeholder="輸入訊息"></textarea>
                <button onclick="send()">發送</button>
            </div>
        </div>
        <script>
            function upd(){{ fetch('/get_channels/'+document.getElementById('g_sel').value).then(r=>r.json()).then(d=>{{
                document.getElementById('c_sel').innerHTML = d.map(c=>`<option value="${{c.id}}">${{c.name}}</option>`).join('');
            }});}}
            function send(){{
                fetch('/broadcast', {{method:'POST', headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{
                    password:document.getElementById('admin_pass').value,
                    message:document.getElementById('msg').value,
                    channel_id:document.getElementById('c_sel').value
                }})}}).then(r=>r.json()).then(d=>alert(d.msg));
            }}
            function act(a){{
                fetch('/', {{method:'POST', headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{
                    password:document.getElementById('admin_pass').value,
                    action:a
                }})}}).then(r=>r.json()).then(d=>{{ if(d.status=='success') location.reload(); else alert(d.msg); }});
            }}
        </script>
    </body>
    </html>
    """

def keep_alive(): Thread(target=lambda: app.run(host='0.0.0.0', port=8080), daemon=True).start()
