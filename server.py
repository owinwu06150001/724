from flask import Flask, request, jsonify, render_template
import os
import psutil
from threading import Thread
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

@app.route('/status', methods=['GET'])
def get_status():
    if _bot and _bot.is_ready():
        bot_status["latency"] = round(_bot.latency * 1000)
        bot_status["cpu"] = psutil.cpu_percent()
        bot_status["ram"] = psutil.virtual_memory().percent
    return jsonify(bot_status)

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
    if request.method == 'POST':
        data = request.json
        if data.get('password') == ADMIN_PASS:
            if data.get('action') == "clear_logs": bot_status["logs"] = []
            elif data.get('action') == "restart": bot_status["restart_requested"] = True
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "msg": "密碼錯誤"})

    guild_list = _bot.guilds if _bot and _bot.is_ready() else []
    
    guild_rows = "".join([
        f'<tr style="border-bottom: 1px solid #2d3748;">'
        f'<td style="padding: 12px; color: #e2e8f0;">{g.name}</td>'
        f'<td style="padding: 12px; color: #94a3b8;">{g.member_count}</td>'
        f'<td style="padding: 12px; color: #10b981; font-weight: 600;">連線中</td>'
        f'</tr>' for g in guild_list
    ])
    
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in guild_list])
    
    log_content = "<br>".join(bot_status['logs']) if bot_status['logs'] else "暫無日誌紀錄..."
    
    return render_template('index.html', 
                           guild_rows=guild_rows, 
                           guild_options=guild_options, 
                           log_content=log_content,
                           bot_status=bot_status)

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
