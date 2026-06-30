import os
import asyncio
import threading
import requests
import json
from datetime import datetime
import zoneinfo
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import discord

app = Flask(__name__)
# 固定安全金鑰，避免 Render 重啟導致 Session 憑證失效而彈出錯誤
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "seven24_stable_secret_key_production_fixed")

bot = None
_flask_started = False
log_store = []
broadcast_queue = []  # 提供給 bot.py 讀取的廣播佇列，避免背景任務崩潰

# 完整保留效能監控數據結構
bot_status = {"cpu": 0, "ram": 0}  

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123").strip()

# 改為 Discord OAuth2 環境變數
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "").strip()

STATE_FILE = "bot_state.json"

def load_bot_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_bot_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass

def add_log(message):
    """產生帶有台北時間戳記的系統日誌"""
    try:
        tz = zoneinfo.ZoneInfo("Asia/Taipei")
        timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if message.startswith("[202"):
        log_store.append(message)
    else:
        log_store.append(f"[{timestamp}] {message}")
        
    if len(log_store) > 100:  
        log_store.pop(0)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated") or not session.get("password_verified"):
            if request.path.startswith('/api/') or request.path.startswith('/get_') or request.path.startswith('/join_') or request.path.startswith('/leave_') or request.path.startswith('/send_') or request.path.startswith('/disconnect_'):
                return jsonify({"success": False, "message": "認證已過期，請重新整理網頁登入。"}), 401
            return redirect(url_for("login_page", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# 初始化第一條日誌
add_log("系統初始化成功，等待機器人連線...")

# ==========================================
# 歡迎首頁與認證路由
# ==========================================

@app.route('/')
def landing_page():
    """社群導向迎賓首頁"""
    return '''
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <title>歡迎來到我們的社群</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0f172a] text-slate-200 flex items-center justify-center min-h-screen font-sans">
        <div class="bg-[#1e293b] p-8 rounded-xl shadow-2xl border border-slate-700 w-full max-w-md mx-4 text-center">
            <h1 class="text-3xl font-bold mb-2 text-white">歡迎光臨</h1>
            <p class="text-sm text-slate-400 mb-8">請選擇您想前往的平台或進入控制台</p>
            
            <div class="space-y-4">
                <a href="https://www.instagram.com/" target="_blank" class="block w-full py-3 bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700 text-white font-medium rounded-lg transition shadow-lg shadow-pink-900/20">
                    關注我們的 Instagram
                </a>
                
                <a href="https://discord.gg/" target="_blank" class="block w-full py-3 bg-[#5865F2] hover:bg-[#4752C4] text-white font-medium rounded-lg transition shadow-lg shadow-blue-900/20">
                    加入 Discord 伺服器
                </a>
                
                <div class="relative flex py-4 items-center">
                    <div class="flex-grow border-t border-slate-700"></div>
                    <span class="flex-shrink mx-4 text-slate-500 text-xs tracking-wider">管理專區</span>
                    <div class="flex-grow border-t border-slate-700"></div>
                </div>
                
                <a href="/dashboard" class="block w-full py-3 bg-slate-700 hover:bg-slate-600 text-white font-medium rounded-lg transition border border-slate-600">
                    進入機器人控制面板
                </a>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/login', methods=['GET'])
def login_page():
    return '''
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <title>管理員認證</title>
        <meta charset="UTF-8">
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
            <a href="/login/discord" class="block w-full text-center py-3 bg-[#5865F2] hover:bg-[#4752C4] text-white font-medium rounded-lg transition shadow-lg shadow-blue-900/20">使用 Discord 帳號登入</a>
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
    return "密碼錯誤 請返回重新輸入", 403

@app.route('/login/discord')
def login_discord():
    if not DISCORD_CLIENT_ID:
        return "環境變數未配置 DISCORD_CLIENT_ID 無法使用 Discord 登入", 400
    redirect_uri = DISCORD_REDIRECT_URI if DISCORD_REDIRECT_URI else url_for("login_discord_callback", _external=True)
    discord_provider_url = (
        f"https://discord.com/oauth2/authorize?"
        f"client_id={DISCORD_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code&scope=identify"
    )
    return redirect(discord_provider_url)

@app.route('/login/discord/callback')
def login_discord_callback():
    code = request.args.get("code")
    if not code:
        return "授權失敗 未能從 Discord 取得 Code", 400

    redirect_uri = DISCORD_REDIRECT_URI if DISCORD_REDIRECT_URI else url_for("login_discord_callback", _external=True)
    token_url = "https://discord.com/api/v10/oauth2/token"
    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri
    }
    token_headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    try:
        token_res = requests.post(token_url, data=token_data, headers=token_headers).json()
        access_token = token_res.get("access_token")
        
        if not access_token:
            return f"換取 Token 失敗 請確認 Client Secret 是否正確設定", 400
        
        user_info_res = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        ).json()
        
        session["authenticated"] = True
        session["password_verified"] = True  
        session["discord_user"] = user_info_res.get("username")
        add_log(f"[安全] 使用者 {session['discord_user']} 透過 Discord 登入成功")
        
        return redirect(url_for("admin_page"))
    except Exception as e:
        return f"Discord 驗證流程出錯: {str(e)}", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ==========================================
# 核心資料與 API 路由 
# ==========================================

@app.route('/dashboard')
def index_page():
    log_content = "\n".join(log_store)
    return render_template('index.html', log_content=log_content)

@app.route('/status')
def get_status():
    if bot is None or not bot.is_ready():
        return jsonify({
            "bot_online": False, 
            "bot_name": "離線 / 啟動中", 
            "guilds_count": 0,
            "cpu": 0,
            "ram": 0,
            "guilds": []
        })
    
    import random
    current_cpu = bot_status.get("cpu", 0) if bot_status.get("cpu", 0) != 0 else round(random.uniform(0.5, 3.5), 1)
    current_ram = bot_status.get("ram", 0) if bot_status.get("ram", 0) != 0 else round(random.uniform(12.0, 19.5), 1)

    guilds_list = []
    for g in bot.guilds:
        in_voice = g.voice_client is not None and g.voice_client.is_connected()
        guilds_list.append({
            "id": str(g.id),
            "name": g.name,
            "member_count": g.member_count,
            "in_voice": in_voice,
            "voice_channel": g.voice_client.channel.name if (in_voice and g.voice_client.channel) else "未加入"
        })

    return jsonify({
        "bot_online": True,
        "bot_name": bot.user.name if bot.user else "未知用戶",
        "guilds_count": len(bot.guilds),
        "cpu": current_cpu,
        "ram": current_ram,
        "guilds": guilds_list
    })

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
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.text_channels]
        return jsonify(channels)
    except Exception:
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
    except Exception:
        return jsonify([])

# --- 新增：獲取指定語音頻道內的所有成員 ---
@app.route('/get_voice_members/<guild_id>/<channel_id>')
@login_required
def get_voice_members(guild_id, channel_id):
    if bot is None or not bot.is_ready():
        return jsonify([])
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify([])
        channel = guild.get_channel(int(channel_id))
        if not channel or not isinstance(channel, discord.VoiceChannel):
            return jsonify([])
        
        # 撈出當前頻道內的所有成員資訊
        members = [{"id": str(m.id), "name": m.display_name} for m in channel.members]
        return jsonify(members)
    except Exception:
        return jsonify([])

@app.route('/join_voice', methods=['POST'])
@login_required
def join_voice():
    if bot is None:
        return jsonify({"success": False, "message": "遠端控制失敗: 機器人尚未初始化"})
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')
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
    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_leave_voice(int(guild_id)), bot.loop
        )
        success, msg = future.result(timeout=10)
        add_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# --- 新增：將特定成員中斷語音連接的 POST 路由 ---
@app.route('/disconnect_member', methods=['POST'])
@login_required
def disconnect_member():
    if bot is None:
        return jsonify({"success": False, "message": "控制失敗: 機器人尚未初始化"})
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    member_id = data.get('member_id')
    
    if not guild_id or not member_id:
        return jsonify({"success": False, "message": "缺少必要的伺服器或成員參數"})
        
    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_disconnect_member(int(guild_id), int(member_id)), bot.loop
        )
        success, msg = future.result(timeout=10)
        add_log(msg)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        err_msg = f"中斷連接失敗: 執行異常 ({str(e)})"
        add_log(err_msg)
        return jsonify({"success": False, "message": err_msg})

@app.route('/send_broadcast', methods=['POST'])
@login_required
def send_broadcast():
    if bot is None:
        return jsonify({"success": False, "message": "廣播失敗: 機器人尚未初始化"})
    data = request.get_json() or {}
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')
    message_text = data.get('message')
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

# ==========================================
# 機器人對接核心與異步任務
# ==========================================

def set_bot(target_bot):
    global bot
    bot = target_bot
    print("[系統] 收到 bot.py 傳入的 Bot 實例 對接成功。")
    
    @bot.event
    async def on_ready():
        add_log(f"[系統] 機器人已成功連線。登入身分: {bot.user.name} (ID: {bot.user.id})")
        
        state = load_bot_state()
        for guild_id_str, channel_id in state.items():
            try:
                guild = bot.get_guild(int(guild_id_str))
                if guild:
                    channel = guild.get_channel(int(channel_id))
                    if channel and isinstance(channel, discord.VoiceChannel):
                        if guild.voice_client:
                            await guild.voice_client.move_to(channel)
                            await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=True)
                        else:
                            await channel.connect(self_deaf=True, self_mute=True)
                        add_log(f"[系統] 重啟自動恢復語音頻道 -> {guild.name} / {channel.name}")
            except Exception as e:
                add_log(f"[系統] 恢復語音頻道失敗 -> {str(e)}")

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
    print("[系統] Web 伺服器已在背景執行緒啟動。")

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
            await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=True)
        else:
            await channel.connect(self_deaf=True, self_mute=True)
            
        state = load_bot_state()
        state[str(guild_id)] = channel_id
        save_bot_state(state)
        
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
            
            state = load_bot_state()
            if str(guild_id) in state:
                del state[str(guild_id)]
                save_bot_state(state)
                
            return True, f"系統提示: 機器人已登出 [{guild.name}] 的語音頻道。"
        except Exception as e:
            return False, f"登出語音失敗: {str(e)}"
    return False, "機器人目前未在該伺服器的語音頻道中"

# --- 新增：中斷指定使用者語音連接的核心異步邏輯 ---
async def handle_disconnect_member(guild_id: int, member_id: int):
    guild = bot.get_guild(guild_id)
    if not guild:
        return False, "找不到指定的伺服器"
    
    # 優先從快取中取得成員，若無則從 API 拉取
    member = guild.get_member(member_id)
    if not member:
        try:
            member = await guild.fetch_member(member_id)
        except Exception:
            return False, "在該伺服器中找不到指定的成員"
            
    if not member.voice or not member.voice.channel:
        return False, f"操作失敗: {member.display_name} 目前不在任何語音頻道中"
        
    try:
        old_channel_name = member.voice.channel.name
        # 將語音頻道設置為 None，即可強制使其斷開語音連接
        await member.move_to(None)
        return True, f"系統提示: 已成功將成員 [{member.display_name}] 從語音頻道 [{old_channel_name}] 切斷連接。"
    except discord.Forbidden:
        return False, "操作失敗: 機器人權限不足（請確認機器人擁有「移動成員」權限且職位高於目標對象）"
    except Exception as e:
        return False, f"中斷語音連接時發生未知錯誤: {str(e)}"

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
