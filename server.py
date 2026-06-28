from flask import Flask, request, jsonify, render_template
import os
import psutil
from threading import Thread
from datetime import datetime

app = Flask(__name__)
_bot = None
broadcast_queue = [] 
voice_queue = [] # 新增：語音指令佇列

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

ADMIN_PASS = os.environ.get("ADMIN_PASS", "111000")
bot_status = {"latency": 0, "cpu": 0, "ram": 0, "logs": [], "restart_requested": False}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    # 1. 改用 append：最新的日誌會加在陣列的最末端（最下面）
    bot_status["logs"].append(f"[{timestamp}] {message}")
    
    # 2. 改用 pop(0)：當超過 100 條時，剔除最前端（最早、最舊）的日誌
    if len(bot_status["logs"]) > 100: 
        bot_status["logs"].pop(0)

@app.route('/status', methods=['GET'])
def get_status():
    if _bot and _bot.is_ready():
        bot_status["latency"] = round(_bot.latency * 1000)
        bot_status["cpu"] = psutil.cpu_percent()
        bot_status["ram"] = psutil.virtual_memory().percent
    return jsonify(bot_status)

@app.route('/get_channels/<int:guild_id>')
def get_channels(guild_id):
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify([])
    # 關鍵修正：將 ch.id 加上 str() 轉為字串，防止前端 JS 精度流失
    channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.text_channels]
    return jsonify(channels)

# 新增：獲取語音頻道列表的 API
@app.route('/get_voice_channels/<int:guild_id>')
def get_voice_channels(guild_id):
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify([])
    # 關鍵修正：將 ch.id 加上 str() 轉為字串，防止前端 JS 精度流失
    channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.voice_channels]
    return jsonify(channels)

@app.route('/broadcast', methods=['POST'])
def broadcast():
    data = request.json
    if data.get('password') != ADMIN_PASS: return jsonify({"status": "error", "msg": "密碼錯誤"})
    broadcast_queue.append({"msg": data.get('message'), "cid": int(data.get('channel_id'))})
    return jsonify({"status": "success", "msg": "訊息已加入佇列"})

# 新增：遠端加入語音頻道的 API
@app.route('/join_voice', methods=['POST'])
def join_voice():
    data = request.json
    if data.get('password') != ADMIN_PASS: return jsonify({"status": "error", "msg": "密碼錯誤"})
    voice_queue.append({
        "action": "join",
        "guild_id": int(data.get('guild_id')),
        "channel_id": int(data.get('channel_id'))
    })
    return jsonify({"status": "success", "msg": "加入語音指令已發送"})

# 修改：首頁改為純狀態顯示，不含管理功能
@app.route('/', methods=['GET'])
def index():
    guild_list = _bot.guilds if _bot and _bot.is_ready() else []
    guild_rows_html = []
    for g in guild_list:
        if g.voice_client:
            status_text = "語音連線中"
            status_color = "#10b981"
        else:
            status_text = "文字待命中"
            status_color = "#94a3b8"

        row = (
            f'<tr style="border-bottom: 1px solid #2d3748;">'
            f'<td style="padding: 12px; color: #e2e8f0;">{g.name}</td>'
            f'<td style="padding: 12px; color: #94a3b8;">{g.member_count}</td>'
            f'<td style="padding: 12px; color: {status_color}; font-weight: 600;">{status_text}</td>'
            f'</tr>'
        )
        guild_rows_html.append(row)
        
    guild_rows = "".join(guild_rows_html)
    log_content = "<br>".join(bot_status['logs']) if bot_status['logs'] else "暫無日誌紀錄..."
    
    return render_template('index.html', guild_rows=guild_rows, log_content=log_content, bot_status=bot_status)

# 新增：管理員專用分頁路由
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        data = request.json
        if data.get('password') == ADMIN_PASS:
            if data.get('action') == "clear_logs": bot_status["logs"] = []
            elif data.get('action') == "restart": bot_status["restart_requested"] = True
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "msg": "密碼錯誤"})

    guild_list = _bot.guilds if _bot and _bot.is_ready() else []
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in guild_list])
    
    return render_template('admin.html', guild_options=guild_options)

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
