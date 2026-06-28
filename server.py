import os
import asyncio
import threading
import secrets
import requests
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import discord

# 1. 設置 Flask 應用程式與金鑰
app = Flask(__name__)
# 建議在 Render 環境變數中設定 FLASK_SECRET_KEY，若無則自動生成隨機金鑰
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

# 宣告全域變數，留空等待 bot.py 透過 set_bot() 注入
bot = None
_flask_started = False
log_store = ["系統初始化成功，等待機器人連線..."]

# ==========================================
# 核心資料控制變數（完整對接 bot.py）
# ==========================================
bot_status = {"cpu": 0, "ram": 0}  
broadcast_queue = []               
voice_queue = []                   

# 安全認證設定（請在 Render 後台設定這些環境變數）
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123") # 預設密碼為 admin123
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

def add_log(message):
    """供內部與 bot.py 紀錄日誌使用"""
    log_store.append(message)
    if len(log_store) > 100:  
        log_store.pop(0)

# ==========================================
# 安全驗證裝飾器 (Decorator)
# ==========================================
def login_required(f):
    """保護路徑：必須同時滿足登入狀態且通過密碼驗證"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 檢查 Session 中是否有安全驗證標記
        if not session.get("authenticated") or not session.get("password_verified"):
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
    <head><title>管理員認證</title></head>
    <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
        <h2>控制台安全安全驗證</h2>
        <div style="max-width: 400px; margin: 0 auto; border: 1px solid #ccc; padding: 20px; border-radius: 8px;">
            <form action="/login/password" method="POST">
                <p>請輸入管理員金鑰：</p>
                <input type="password" name="password" placeholder="請輸入密碼" style="width: 80%; padding: 10px; margin-bottom: 15px;"><br>
                <button type="submit" style="width: 85%; padding: 10px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">使用密碼登入</button>
            </form>
            <hr style="margin: 20px 0;">
            <a href="/login/google"><button style="width: 85%; padding: 10px; background-color: #db4437; color: white; border: none; border-radius: 4px; cursor: pointer;">使用 Google 帳號登入</button></a>
        </div>
    </body>
    </html>
    '''

@app.route('/login/password', methods=['POST'])
def login_password():
    input_password = request.form.get("password")
    if input_password == ADMIN_PASSWORD:
        session["password_verified"] = True
        session["authenticated"] = True  # 直接賦予完整權限
        return redirect(url_for("admin_page"))
    return "密碼錯誤，請返回重新輸入", 403

@app.route('/login/google')
def login_google():
    if not GOOGLE_CLIENT_ID:
        return "環境變數未配置 GOOGLE_CLIENT_ID，無法使用 Google 登入。", 400
    
    # 建立 Google OAuth 導向網址
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
    
    # 向 Google 交換 Token
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
        
        # 獲取使用者基本資料
        user_info_res = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        ).json()
        
        # 登入成功，儲存資訊
        session["authenticated"] = True
        session["user_email"] = user_info_res.get("email")
        add_log(f"[安全] 使用者 {session['user_email']} 透過 Google 登入成功。")
        
        # 根據安全邏輯：Google 驗證成功後，仍需導回輸入管理員密碼
        if not session.get("password_verified"):
            return '''
            <script>
                alert("Google 驗證成功！請接續輸入管理員密碼以解鎖頁面。");
                window.location.href = "/login";
            </script>
            '''
        return redirect(url_for("admin_page"))
    except Exception as e:
        return f"Google 驗證流程出錯: {str(e)}", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ==========================================
# 網頁路由區塊
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
    """供網頁前端即時更新日誌"""
    return jsonify({"logs": log_store})

@app.route('/api/queues')
@login_required
def get_queues_api():
    """獲取目前的廣播與語音排隊狀態"""
    return jsonify({
        "broadcast_queue": broadcast_queue,
        "voice_queue": voice_queue
    })

@app.route('/get_channels/<guild_id>')
@login_required
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
        return jsonify([])

@app.route('/get_voice_channels/<guild_id>')
@login_required
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
        return jsonify([])

@app.route('/join_voice', methods=['POST'])
@login_required
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

@app.route('/leave_voice', methods=['POST'])
@login_required
def leave_voice():
    """切斷機器人與指定伺服器的語音連線"""
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    if not guild_id:
        return jsonify({"success": False, "message": "缺少伺服器 ID"})
    
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
# 供外部 (bot.py) 呼叫的核心對接程序
# ==========================================

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
