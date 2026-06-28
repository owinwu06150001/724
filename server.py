import asyncio
import threading
import logging
from flask import Flask, render_template, jsonify, request
import discord
from discord.ext import commands

# 1. 設置 Flask 應用程式
app = Flask(__name__)

# 2. 設置 Discord 機器人 (確保開啟必要意圖)
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 模擬內部日誌儲存空間
log_store = ["系統初始化成功，等待機器人連線..."]

def append_log(message):
    log_store.append(message)
    if len(log_store) > 100:  # 限制快取日誌數量
        log_store.pop(0)

# ==========================================
# Flask 網頁路由區塊
# ==========================================

@app.route('/')
def index_page():
    # 首頁：展示基本監控狀態與日誌
    log_content = "\n".join(log_store)
    return render_template('index.html', log_content=log_content)

@app.route('/status')
def get_status():
    # 提供給首頁動態更新的 JSON 狀態
    status_data = {
        "bot_online": bot.is_ready(),
        "bot_name": bot.user.name if bot.user else "未連線",
        "guilds_count": len(bot.guilds)
    }
    return jsonify(status_data)

@app.route('/admin')
def admin_page():
    # 管理後台：獲取伺服器清單並【強制將 ID 轉為字串】
    if not bot.is_ready():
        guilds_data = []
    else:
        guilds_data = [{"id": str(g.id), "name": g.name} for g in bot.guilds]
    
    log_content = "\n".join(log_store)
    return render_template('admin.html', guilds=guilds_data, log_content=log_content)

@app.route('/get_channels/<guild_id>')
def get_channels(guild_id):
    # 獲取特定伺服器的文字頻道清單
    if not bot.is_ready():
        return jsonify([])
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        # 將 ID 強制轉字串回傳，防止前端 JS 精度流失
        channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.text_channels]
        return jsonify(channels)
    except Exception as e:
        print(f"獲取文字頻道失敗: {e}")
        return jsonify([])

@app.route('/get_voice_channels/<guild_id>')
def get_voice_channels(guild_id):
    # 獲取特定伺服器的語音頻道清單
    if not bot.is_ready():
        return jsonify([])
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        # 將 ID 強制轉字串回傳，防止前端 JS 精度流失
        voice_channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.voice_channels]
        return jsonify(voice_channels)
    except Exception as e:
        print(f"獲取語音頻道失敗: {e}")
        return jsonify([])

@app.route('/join_voice', methods=['POST'])
def join_voice():
    # 控制機器人加入語音頻道
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')

    if not guild_id or not channel_id:
        return jsonify({"success": False, "message": "無效的伺服器或頻道參數"})

    try:
        # 在獨立的 asyncio 事件迴圈安全調用機器人動作
        future = asyncio.run_coroutine_threadsafe(
            handle_join_voice(int(guild_id), int(channel_id)), bot.loop
        )
        success, msg = future.result(timeout=10)
        append_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        err_msg = f"遠端控制失敗: 執行非同步調度異常 ({str(e)})"
        append_log(err_msg)
        return jsonify({"success": False, "message": err_msg})

@app.route('/send_broadcast', methods=['POST'])
def send_broadcast():
    # 控制機器人發送文字廣播
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
        append_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        err_msg = f"文字廣播失敗: 執行非同步調度異常 ({str(e)})"
        append_log(err_msg)
        return jsonify({"success": False, "message": err_msg})

# ==========================================
# Discord 機器人非同步異步處理核心
# ==========================================

async def handle_join_voice(guild_id: int, channel_id: int):
    guild = bot.get_guild(guild_id)
    if not guild:
        return False, f"遠端控制失敗: 找不到目標伺服器或語音頻道 (ID: {guild_id})"
    
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

@bot.event
async def on_ready():
    append_log(f"[系統] 機器人已成功連線。登入身分: {bot.user.name} (ID: {bot.user.id})")

# ==========================================
# 雙核心啟動程序
# ==========================================

def run_flask():
    # 關閉 Werkzeug 預設內部重載器以確保單一執行緒乾淨啟動
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    # 1. 於背景執行緒啟動 Flask 網頁伺服器
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. 於主執行緒啟動 Discord 機器人 (請替換為您的 Discord Token)
    # bot.run('YOUR_DISCORD_BOT_TOKEN_HERE')
    print("請記得在 server.py 底部填入真實的 Token 以進行正式運行。")
