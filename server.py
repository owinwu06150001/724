import os
import asyncio
import threading
import secrets
import requests
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import discord

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

bot = None
_flask_started = False
log_store = ["系統初始化成功，等待機器人連線..."]

bot_status = {"cpu": 0, "ram": 0}  
broadcast_queue = []               
voice_queue = []                   

# 使用 .strip() 確保清除環境變數中不小心夾帶的換行符 (\n) 或空白
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123").strip()
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

def add_log(message):
    log_store.append(message)
    if len(log_store) > 100:  
        log_store.pop(0)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated") or not session.get("password_verified"):
            # 如果是前端 AJAX 的 API 請求，改回傳 JSON 錯誤而非 HTML 導向，防止前端解析崩潰
            if request.path.startswith('/api/') or request.path.startswith('/get_') or request.path.startswith('/join_') or request.path.startswith('/leave_') or request.path.startswith('/send_'):
                return jsonify({"success": False, "message": "認證已過期或未登入，請重新整理網頁。"}), 401
            return redirect(url_for("login_page", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# 認證與登入路由
# ==========================================

@app.route('/login', methods=['GET'])
def login_page():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>管理員認證</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0f172a] text-slate-200 flex items-center justify-center min-h-screen font-sans">
        <div class="bg-[#1e293b] p-8 rounded-xl shadow-2xl border border-slate-700 w-full max-w-md mx-4">
            <h2 class="text-2xl font-bold text-center mb-6 text-white border-b border-slate-700 pb-3">控制台安全驗證</h2>
            <form action="/login/password" method="POST" class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-slate-400 mb-2">請輸入管理員金鑰</label>
                    <input type="password" name="password" placeholder="請輸入密碼" class="w-full px-4 py-3 bg-[#0f172a] border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500 text-white placeholder-slate-500 transition">
                </div>
                <button type="submit" class="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition shadow-lg shadow-blue-900/30">使用密碼登入</button>
            </form>
            <div class="relative flex py-5 items-center">
                <div class="flex-grow border-t border-slate-700"></div>
                <span class="flex-shrink mx-4 text-slate-500 text-sm">或</span>
                <div class="flex-grow border-t border-slate-700"></div>
            </div>
            <a href="/login/google" class="block w-full text-center py-3 bg-[#db4437] hover:bg-[#c53929] text-white font-medium rounded-lg transition shadow-lg shadow-red-900/20">使用 Google 帳號登入</a>
        </div>
    </body>
    </html>
    '''

@app.route('/login/password', methods=['POST'])
def login_password():
    input_password = request.form.get("password")
    if input_password == ADMIN_PASSWORD:
        session["password_verified"] = True
        session["authenticated"] = True
        return redirect(url_for("admin_page"))
    return "密碼錯誤，請返回重新輸入", 403

@app.route('/login/google')
def login_google():
    if not GOOGLE_CLIENT_ID:
        return "環境變數未配置 GOOGLE_CLIENT_ID，無法使用 Google 登入。", 400
    redirect_uri = url_for("login_google_callback", _external=True)
    google_provider_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"response_type=code&client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&scope=openid%20email%20profile"
    )
    return redirect(google_provider_url)

@app.route('/login/google/callback')
def login_google_callback():
    code = request.args.get("code")
    if not code:
        return "授權失敗，未能從 Google 取得 Code", 400

    redirect_uri = url_for("login_google_callback", _external=True)
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    
    try:
        token_res = requests.post(token_url, data=token_data).json()
        access_token = token_res.get("access_token")
        
        user_info_res = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        ).json()
        
        session["authenticated"] = True
        session["password_verified"] = True  # 直接賦予完整權限
        session["user_email"] = user_info_res.get("email")
        add_log(f"[安全] 使用者 {session['user_email']} 透過 Google 登入成功。")
        
        return redirect(url_for("admin_page"))
    except Exception as e:
        return f"Google 驗證流程出錯: {str(e)}", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ==========================================
# 網頁路由與安全 API 區塊
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
@login_required
def admin_page():
    if bot is None or not bot.is_ready():
        guilds_data = []
    else:
        guilds_data = [{"id": str(g.id), "name": g.name} for g in bot.guilds]
    log_content = "\n".join(log_store)
    return render_template('admin.html', guilds=guilds_data, log_content=log_content)

@app.route('/api/logs')
def get_logs_api():
    return jsonify({"logs": log_store})

@app.route('/get_channels/<guild_id>')
@login_required
def get_channels(guild_id):
    if bot is None or not bot.is_ready():
        return jsonify([])
    if not guild_id or not guild_id.isdigit():
        return jsonify([])
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.text_channels]
        return jsonify(channels)
    except Exception as e:
        return jsonify([])

@app.route('/get_voice_channels/<guild_id>')
@login_required
def get_voice_channels(guild_id):
    if bot is None or not bot.is_ready():
        return jsonify([])
    if not guild_id or not guild_id.isdigit():
        return jsonify([])
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        voice_channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.voice_channels]
        return jsonify(voice_channels)
    except Exception as e:
        return jsonify([])

@app.route('/join_voice', methods=['POST'])
@login_required
def join_voice():
    if bot is None:
        return jsonify({"success": False, "message": "遠端控制失敗: 機器人尚未初始化"})
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')
    if not guild_id or not channel_id or not str(guild_id).isdigit() or not str(channel_id).isdigit():
        return jsonify({"success": False, "message": "無效的伺服器或頻道參數"})
    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_join_voice(int(guild_id), int(channel_id)), bot.loop
        )
        success, msg = future.result(timeout=10)
        add_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        err_msg = f"遠端控制失敗: 執行異常 ({str(e)})"
        add_log(err_msg)
        return jsonify({"success": False, "message": err_msg})

@app.route('/leave_voice', methods=['POST'])
@login_required
def leave_voice():
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    if not guild_id or not str(guild_id).isdigit():
        return jsonify({"success": False, "message": "缺少或無效的伺服器 ID"})
    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_leave_voice(int(guild_id)), bot.loop
        )
        success, msg = future.result(timeout=10)
        add_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/send_broadcast', methods=['POST'])
@login_required
def send_broadcast():
    if bot is None:
        return jsonify({"success": False, "message": "廣播失敗: 機器人尚未初始化"})
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')
    message_text = data.get('message')
    if not guild_id or not channel_id or not message_text or not str(guild_id).isdigit() or not str(channel_id).isdigit():
        return jsonify({"success": False, "message": "參數填寫不完整或格式錯誤"})
    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_send_message(int(guild_id), int(channel_id), message_text), bot.loop
        )
        success, msg = future.result(timeout=10)
        add_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        err_msg = f"文字廣播失敗: 執行異常 ({str(e)})"
        add_log(err_msg)
        return jsonify({"success": False, "message": err_msg})

def set_bot(target_bot):
    global bot
    bot = target_bot
    print("[系統] 收到 bot.py 傳入的 Bot 實例，對接成功。")
    @bot.event
    async def on_ready():
        add_log(f"[系統] 機器人已成功連線。登入身分: {bot.user.name} (ID: {bot.user.id})")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    global _flask_started
    if _flask_started:
        return
    _flask_started = True
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("[系統] Web 伺服器已在背景執行緒啟動 (keep_alive)。")

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

async def handle_leave_voice(guild_id: int):
    guild = bot.get_guild(guild_id)
    if not guild:
        return False, "找不到指定的伺服器"
    if guild.voice_client:
        try:
            await guild.voice_client.disconnect()
            return True, f"系統提示: 機器人已登出 [{guild.name}] 的語音頻道。"
        except Exception as e:
            return False, f"登出語音失敗: {str(e)}"
    return False, "機器人目前未在該伺服器的語音頻道中"

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
