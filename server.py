import os
import asyncio
import threading
from flask import Flask, render_template, jsonify, request
import discord

# 1. 設置 Flask 應用程式
app = Flask(__name__)

# 宣告全域變數，留空等待 bot.py 透過 set_bot() 注入
bot = None

# 防止 Web 伺服器重複啟動的旗標
_flask_started = False

# 模擬內部日誌儲存空間
log_store = ["系統初始化成功，等待機器人連線..."]

# ==========================================
# 補齊 bot.py 背景任務需要的核心變數
# ==========================================
bot_status = {"cpu": 0, "ram": 0}  # 供 bot.py 第 238 行更新硬體數據
broadcast_queue = []               # 供 bot.py 第 157 行讀取廣播排隊

def add_log(message):
    """供 bot.py 記錄日誌使用"""
    log_store.append(message)
    if len(log_store) > 100:  # 限制快取日誌數量
        log_store.pop(0)

# ==========================================
# 供外部 (bot.py) 呼叫的核心對接程序
# ==========================================

def set_bot(target_bot):
    """接收來自 bot.py 的 Bot 實例，並動態註冊事件"""
    global bot
    bot = target_bot
    print("[系統] 收到 bot.py 傳入的 Bot 實例，對接成功。")

    # 動態綁定機器人上線事件
    @bot.event
    async def on_ready():
        add_log(f"[系統] 機器人已成功連線。登入身分: {bot.user.name} (ID: {bot.user.id})")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    """在背景獨立執行緒啟動 Flask 網頁伺服器（內含重複啟動防護）"""
    global _flask_started
    if _flask_started:
        print("[系統] Web 伺服器已經在執行中，跳過重複啟動程序。")
        return
    
    _flask_started = True
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("[系統] Web 伺服器已在背景執行緒啟動 (keep_alive)。")


# ==========================================
# Flask 網頁路由區塊
# ==========================================

@app.route('/')
def index_page():
    log_content = "\n".join(log_store)
    return render_template('index.html', log_content=log_content)

@app.route('/status')
def get_status():
    if bot is None:
        return jsonify({"bot_online": False, "bot_name": "準備中...", "guilds_count": 0, "cpu": 0, "ram": 0})
        
    status_data = {
        "bot_online": bot.is_ready(),
        "bot_name": bot.user.name if bot.user else "未連線",
        "guilds_count": len(bot.guilds) if bot.is_ready() else 0,
        "cpu": bot_status.get("cpu", 0),
        "ram": bot_status.get("ram", 0)
    }
    return jsonify(status_data)

@app.route('/admin')
def admin_page():
    if bot is None or not bot.is_ready():
        guilds_data = []
    else:
        guilds_data = [{"id": str(g.id), "name": g.name} for g in bot.guilds]
    
    log_content = "\n".join(log_store)
    return render_template('admin.html', guilds=guilds_data, log_content=log_content)

@app.route('/get_channels/<guild_id>')
def get_channels(guild_id):
    if bot is None or not bot.is_ready():
        return jsonify([])
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.text_channels]
        return jsonify(channels)
    except Exception as e:
        print(f"獲取文字頻道失敗: {e}")
        return jsonify([])

@app.route('/get_voice_channels/<guild_id>')
def get_voice_channels(guild_id):
    if bot is None or not bot.is_ready():
        return jsonify([])
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        voice_channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.voice_channels]
        return jsonify(voice_channels)
    except Exception as e:
        print(f"獲取語音頻道失敗: {e}")
        return jsonify([])

@app.route('/join_voice', methods=['POST'])
def join_voice():
    if bot is None:
        return jsonify({"success": False, "message": "遠端控制失敗: 機器人尚未初始化"})

    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')

    if not guild_id or not channel_id:
        return jsonify({"success": False, "message": "無效的伺服器或頻道參數"})

    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_join_voice(int(guild_id), int(channel_id)), bot.loop
        )
        success, msg = future.result(timeout=10)
        add_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        err_msg = f"遠端控制失敗: 執行非同步調度異常 ({str(e)})"
        add_log(err_msg)
        return jsonify({"success": False, "message": err_msg})

@app.route('/send_broadcast', methods=['POST'])
def send_broadcast():
    if bot is None:
        return jsonify({"success": False, "message": "廣播失敗: 機器人尚未初始化"})

    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')
    message_text = data.get('message')

    if not guild_id or not channel_id or not message_text:
        return jsonify({"success": False, "message": "參數填寫不完整"})

    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_send_message(int(guild_id), int(channel_id), message_text), bot.loop
        )
        success, msg = future.result(timeout=10)
        add_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        err_msg = f"文字廣播失敗: 執行非同步調度異常 ({str(e)})"
        add_log(err_msg)
        return jsonify({"success": False, "message": err_msg})

# ==========================================
# Discord 機器人非同步異步處理核心
# ==========================================

async def handle_join_voice(guild_id: int, channel_id: int):
    guild = bot.get_guild(guild_id)
    if not guild:
        return False, f"遠端控制失敗: 找不到目標伺服器 (ID: {guild_id})"
    
    channel = guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.VoiceChannel):
        return False, f"遠端控制失敗: 找不到目標語音頻道 (ID: {channel_id})"

    try:
        if guild.voice_client:
            await guild.voice_client.move_to(channel)
        else:
            await channel.connect()
        return True, f"系統提示: 成功調動機器人加入語音頻道 -> {guild.name} / {channel.name}"
    except Exception as e:
        return False, f"遠端控制失敗: 無法建立語音連接 ({str(e)})"

async def handle_send_message(guild_id: int, channel_id: int, text: str):
    guild = bot.get_guild(guild_id)
    if not guild:
        return False, f"廣播失敗: 找不到目標伺服器 (ID: {guild_id})"
    
    channel = guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return False, f"廣播失敗: 找不到或無權限存取文字頻道 (ID: {channel_id})"

    try:
        await channel.send(text)
        return True, f"系統提示: 成功向 [{guild.name} / {channel.name}] 發送廣播訊息"
    except Exception as e:
        return False, f"廣播失敗: 訊息發送遺失 ({str(e)})"
