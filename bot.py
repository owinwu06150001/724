import discord
from discord.ext import commands, tasks
from discord.ext import tasks
from discord import app_commands
from typing import Optional
import os
import time
import asyncio
import datetime
import psutil
import static_ffmpeg
import server
import json
import logging

static_ffmpeg.add_paths()
server.keep_alive()

def log_event(msg):
    server.add_log(msg)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.presences = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

server.set_bot(bot)
server.keep_alive()

stay_channels = {}
stay_since = {}
tag_targets = {}
stats_channels = {}
welcome_channels = {} 
voice_log_channels = {} 
start_time = datetime.datetime.now()
status_toggle = True
voice_client = await channel.connect(self_deafen=True)

AUDIT_LOG_ACTIONS_CN = {
    "guild_update": "更新伺服器", "channel_create": "建立頻道", "channel_update": "更新頻道",
    "channel_delete": "刪除頻道", "member_kick": "踢出成員", "member_ban": "封鎖成員",
    "member_unban": "解除封鎖", "member_update": "更新成員", "member_role_update": "更新成員身分組",
    "role_create": "建立身分組", "role_update": "更新身分組", "role_delete": "刪除身分組",
    "message_delete": "刪除訊息", "message_bulk_delete": "批量刪除訊息",
}

# 完整的翻譯字典，請確保結尾的 } 沒有被漏掉
LOG_TRANSLATIONS = {
    "Connecting to voice...": "正在連線至語音頻道...",
    "Starting voice handshake...": "開始語音連線握手程序...",
    "Voice handshake complete.": "語音連線握手完成。",
    "Voice connection complete.": "語音連線建立成功。",
    "Disconnected from voice... Reconnecting in": "已從語音頻道斷線... 準備重新連線，倒數",
    "The voice handshake is being terminated": "語音連線握手程序已被強制終止",
    "WebSocket closed with": "WebSocket 連線已關閉，代碼:",
    "Shard ID None WebSocket closed with 1006": "連線異常中斷 (錯誤代碼 1006，通常為網路不穩定)",
    "We have successfully connected to the gateway.": "已成功連線至 Discord 網關。"
}

class WebDashboardHandler(logging.Handler):
    def emit(self, record):
        log_msg = self.format(record)
        
        # 進行字串替換翻譯
        for eng, cht in LOG_TRANSLATIONS.items():
            if eng in log_msg:
                log_msg = log_msg.replace(eng, cht)
                
        # 傳送至網頁端
        server.add_log(log_msg)

# 設定 Discord 函式庫的日誌記錄器
logger = logging.getLogger('discord')
logger.setLevel(logging.INFO)

# 建立並加入自訂處理器
web_handler = WebDashboardHandler()
web_handler.setFormatter(logging.Formatter('[系統] %(message)s'))
logger.addHandler(web_handler)

def get_help_text(bot_mention):
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 24/7 語音掛機設計具備 隨時斷線機制。\n\n"
        "### 語音\n"
        "* /加入 [頻道]：進入語音頻道掛機。\n"
        "* /離開：退出頻道並停止掛機。\n"
        "* /狀態：查看掛機時間與延遲。\n\n"
        "### 伺服器管理\n"
        "* /設定統計頻道：建立自動更新人數的統計頻道。\n"
        "* /刪除統計頻道：一鍵刪除人數統計頻道與分類。\n"
        "* /設定歡迎頻道 [頻道]：設定歡迎訊息發送位置。\n"
        "* /查看審核日誌：查看操作紀錄。\n\n"
        "### 身分組管理\n"
        "* /給予身分組 [成員] [身分組]：賦予成員身分組。\n"
        "* /移除身分組 [成員] [身分組]：移除成員的身分組。\n"
        "* /建立身分組面板 [身分組] [圖片網址]：發送按鈕面板。\n\n"
        "### 工具\n"
        "* /開始標註 [成員] [內容] [次數]：執行標註轟炸。\n"
        "* /停止標註 [成員]：結束轟炸。\n"
        "* /系統狀態：查看硬體資訊。\n"
        "* /使用方式：顯示本手冊。"
    )

class RoleButtonView(discord.ui.View):
    def __init__(self, role_id):
        super().__init__(timeout=None)
        self.role_id = role_id
    @discord.ui.button(label="取得身分組", style=discord.ButtonStyle.success, custom_id="role_add_persistent")
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = interaction.guild.get_role(self.role_id)
        if role:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"已獲取 {role.name} 身分組", ephemeral=True)
    @discord.ui.button(label="移除身分組", style=discord.ButtonStyle.danger, custom_id="role_remove_persistent")
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = interaction.guild.get_role(self.role_id)
        if role:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"已移除 {role.name} 身分組", ephemeral=True)



async def tag_logic(channel, target, content, times):
    for i in range(times):
        if tag_targets.get(target.id) is False: break
        try: await channel.send(f"{target.mention} {content}")
        except: break
        await asyncio.sleep(0.8)

@tasks.loop(minutes=1)
async def update_status():
    global status_toggle
    print("正在嘗試更新機器人狀態...") # 增加這一行偵錯
    
    if status_toggle:
        uptime = datetime.datetime.now() - start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"已運行: {days}天 {hours}時 {minutes}分")
    else:
        guild_count = len(bot.guilds)
        activity = discord.Activity(type=discord.ActivityType.playing, name=f"服務於 {guild_count} 個伺服器")
    
    await bot.change_presence(activity=activity)
    status_toggle = not status_toggle
    

@tasks.loop(seconds=3)
async def check_broadcast():
    if len(server.broadcast_queue) > 0:
        item = server.broadcast_queue.pop(0)
        channel = bot.get_channel(item["cid"])
        if channel:
            try:
                await channel.send(item["msg"])
                server.add_log(f"已發送至 #{channel.name}: {item['msg']}")
            except Exception as e:
                server.add_log(f"發送失敗: {e}")
        else:
            server.add_log("找不到頻道，無法發送")
    if server.voice_queue:
        job = server.voice_queue.pop(0)
        if job.get('action') == "join":
            g_id = job.get('guild_id')
            c_id = job.get('channel_id')
            
            guild = bot.get_guild(g_id)
            channel = bot.get_channel(c_id)
            
            # 如果快取找不到頻道，嘗試從伺服器取得
            if guild and not channel:
                channel = guild.get_channel(c_id)
            if not channel:
                try:
                    channel = await bot.fetch_channel(c_id)
                except:
                    channel = None
            
            # 執行加入或移動
            if guild and channel:
                try:
                    if guild.voice_client:
                        await guild.voice_client.move_to(channel)
                        server.add_log(f"遠端控制: 機器人已移動至語音頻道「{channel.name}」")
                    else:
                        await channel.connect()
                        server.add_log(f"遠端控制: 成功加入語音頻道「{channel.name}」")
                except Exception as e:
                    server.add_log(f"遠端控制失敗: 無法進入語音頻道，原因: {e}")
            else:
                server.add_log(f"遠端控制失敗: 找不到目標伺服器或語音頻道 (ID: {c_id})")
@tasks.loop(seconds=5)
async def check_restart():
    if hasattr(server, 'bot_status') and server.bot_status.get("restart_requested"):
        log_event("收到重啟指令...")
        await bot.close()
        os._exit(0)

@tasks.loop(seconds=1)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild: continue
        vc = guild.voice_client
        if vc and vc.is_connected(): continue
        if vc:
            try: await vc.disconnect()
            except: pass
        ch = bot.get_channel(cid)
        if ch:
            try: await ch.connect(self_deaf=True)
            except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds:
        if guild.id in stats_channels:
            stats = stats_channels[guild.id]
            total, bots = guild.member_count, len([m for m in guild.members if m.bot])
            humans, online = total - bots, len([m for m in guild.members if m.status != discord.Status.offline])
            data_map = {"total": f"全部人數: {total}", "humans": f"成員人數: {humans}", "online": f"在線成員: {online}", "bots": f"機器人: {bots}"}
            for key, name in data_map.items():
                if key in stats:
                    ch = bot.get_channel(stats[key])
                    if ch:
                        try: await ch.edit(name=name)
                        except: pass

@tasks.loop(minutes=1)
async def update_web_stats():
    server.bot_status["cpu"] = psutil.cpu_percent()
    server.bot_status["ram"] = psutil.virtual_memory().percent
    server.bot_status["latency"] = round(bot.latency * 1000)
    server.bot_status["guild_count"] = len(bot.guilds)

@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user.mentioned_in(message) and message.mention_everyone is False:
        await message.channel.send(get_help_text(bot.user.mention))
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    channel_id = welcome_channels.get(member.guild.id)
    if channel_id:
        channel = bot.get_channel(channel_id)
        if channel:
            embed = discord.Embed(title="歡迎訊息", description=f"你好 歡迎加入 {member.guild.name}！\n\n{member.mention}\n\n你是本伺服器的第 {member.guild.member_count} 位成員", color=0xaa96da)
            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

@bot.event
async def on_voice_state_update(member, before, after):
    channel_id = voice_log_channels.get(member.guild.id)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    # 偵測加入頻道
    if before.channel is None and after.channel is not None:
        voice_join_times[member.id] = datetime.datetime.now()
        embed = discord.Embed(title="加入語音頻道", color=0x00ff00)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="玩家", value=member.mention, inline=False)
        embed.add_field(name="加入頻道", value=after.channel.name, inline=False)
        embed.timestamp = datetime.datetime.now()
        await channel.send(embed=embed)

    # 偵測離開頻道
    elif before.channel is not None and after.channel is None:
        join_time = voice_join_times.pop(member.id, None)
        duration_text = "無法計算"
        
        if join_time:
            duration = datetime.datetime.now() - join_time
            total_seconds = int(duration.total_seconds())
            minutes, seconds = divmod(total_seconds, 60)
            duration_text = f"{minutes} 分 {seconds} 秒"

        embed = discord.Embed(title="離開語音頻道", color=0xff0000)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="玩家", value=member.mention, inline=False)
        embed.add_field(name="離開頻道", value=before.channel.name, inline=False)
        embed.add_field(name="停留時間", value=duration_text, inline=False)
        embed.timestamp = datetime.datetime.now()
        await channel.send(embed=embed)

@bot.event
async def on_ready():
    server.set_bot(bot)
    if not update_web_stats.is_running(): update_web_stats.start()
    if not check_connection.is_running(): check_connection.start()
    if not update_member_stats.is_running(): update_member_stats.start()
    if not check_restart.is_running(): check_restart.start()
    if not check_broadcast.is_running():
        check_broadcast.start()
    update_status.start()
    await bot.tree.sync()
    print(f"機器人已登入: {bot.user}")

# ---------------------------------------------------------------------------
# 以下為指令區塊：將 加入 與 離開 置於最上方
# ---------------------------------------------------------------------------

@tree.command(name="加入", description="進入語音頻道掛機")
async def join_vc(interaction: discord.Interaction, 頻道: discord.VoiceChannel = None):
    頻道 = 頻道 or (interaction.user.voice.channel if interaction.user.voice else None)
    if not 頻道: return await interaction.response.send_message("請先進入頻道或指定頻道", ephemeral=True)
    await 頻道.connect(self_deaf=True)
    stay_channels[interaction.guild.id] = 頻道.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.response.send_message(f"已連接至：{頻道.name}")

@tree.command(name="離開", description="退出語音")
async def leave_vc(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        await interaction.response.send_message("已離開語音頻道")
    else: await interaction.response.send_message("目前不在語音中")

# ---------------------------------------------------------------------------
# 其他指令
# ---------------------------------------------------------------------------

@tree.command(name="系統狀態", description="硬體監控與執行時間")
async def sys_info(interaction: discord.Interaction):
    # 計算運行時間
    uptime = datetime.datetime.now() - start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}天 {hours}小時 {minutes}分 {seconds}秒"
    
    # 顯示狀態
    await interaction.response.send_message(
        f"CPU: {psutil.cpu_percent()}% | RAM: {psutil.virtual_memory().percent}%\n"
        f"已運行: {uptime_str}"
    )

@tree.command(name="使用方式", description="顯示功能清單")
async def show_help(interaction: discord.Interaction):
    await interaction.response.send_message(get_help_text(bot.user.mention))

@tree.command(name="建立身分組面板", description="發送身分組按鈕面板")
@app_commands.checks.has_permissions(manage_roles=True)
async def setup_role_panel(interaction: discord.Interaction, 身分組: discord.Role, 圖片網址: str = None):
    view = RoleButtonView(身分組.id)
    embed = discord.Embed(title="身分組", description=f"點擊下方按鈕可 獲取/移除 {身分組.mention} 身分組", color=0xaa96da)
    if 圖片網址: embed.set_thumbnail(url=圖片網址)
    bot.add_view(view)
    await interaction.response.send_message(embed=embed, view=view)

@tree.command(name="設定歡迎頻道", description="設定歡迎訊息發送位置")
@app_commands.checks.has_permissions(manage_guild=True)
async def set_welcome_channel(interaction: discord.Interaction, 頻道: discord.TextChannel):
    welcome_channels[interaction.guild.id] = 頻道.id
    await interaction.response.send_message(f"歡迎頻道已設定為：{頻道.mention}")

@tree.command(name="開始標註", description="對成員執行轟炸")
async def start_bomb(interaction: discord.Interaction, 成員: discord.Member, 次數: int, 內容: Optional[str] = "戳一下"):
    if 次數 <= 0: return await interaction.response.send_message("次數必須大於0", ephemeral=True)
    if 次數 > 50: return await interaction.response.send_message("次數過多，請限制在 50 次以內", ephemeral=True)
    
    tag_targets[成員.id] = True
    await interaction.response.send_message(f"開始轟炸 {成員.mention}")
    await tag_logic(interaction.channel, 成員, 內容, 次數)
    tag_targets[成員.id] = False

@tree.command(name="停止標註", description="停止轟炸")
async def stop_bomb(interaction: discord.Interaction, 成員: discord.Member):
    tag_targets[成員.id] = False
    await interaction.response.send_message(f"已停止對 {成員.mention} 的動作")

@tree.command(name="設定統計頻道", description="建立人數統計頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def stats_setup(interaction: discord.Interaction):
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True), guild.me: discord.PermissionOverwrite(connect=True, view_channel=True, manage_channels=True)}
    category = await guild.create_category("伺服器數據", position=0, overwrites=overwrites)
    total, bots = guild.member_count, len([m for m in guild.members if m.bot])
    humans, online = total - bots, len([m for m in guild.members if m.status != discord.Status.offline])
    c_total = await guild.create_voice_channel(f"全部人數: {total}", category=category, overwrites=overwrites)
    c_humans = await guild.create_voice_channel(f"成員人數: {humans}", category=category, overwrites=overwrites)
    c_online = await guild.create_voice_channel(f"在線成員: {online}", category=category, overwrites=overwrites)
    c_bots = await guild.create_voice_channel(f"機器人: {bots}", category=category, overwrites=overwrites)
    stats_channels[guild.id] = {"total": c_total.id, "humans": c_humans.id, "online": c_online.id, "bots": c_bots.id}
    await interaction.response.send_message("統計頻道建立完成")

@tree.command(name="刪除統計頻道", description="一鍵刪除人數統計頻道與分類")
@app_commands.checks.has_permissions(manage_channels=True)
async def stats_delete(interaction: discord.Interaction):
    guild = interaction.guild
    if guild.id not in stats_channels:
        return await interaction.response.send_message("未偵測到本伺服器的統計頻道紀錄", ephemeral=True)
    
    await interaction.response.defer(thinking=True)
    stats = stats_channels[guild.id]
    category_to_delete = None
    
    for key, cid in stats.items():
        ch = guild.get_channel(cid)
        if ch:
            if not category_to_delete and ch.category:
                category_to_delete = ch.category
            try:
                await ch.delete()
            except:
                pass
                
    if category_to_delete:
        try:
            await category_to_delete.delete()
        except:
            pass
            
    stats_channels.pop(guild.id, None)
    await interaction.followup.send("統計頻道及分類已全數刪除完成")

@tree.command(name="給予身分組", description="賦予成員身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_add(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    try:
        await 成員.add_roles(身分組)
        await interaction.response.send_message(f"已將 {身分組.name} 給予 {成員.display_name}")
    except Exception as e: await interaction.response.send_message(f"失敗: {e}")

@tree.command(name="移除身分組", description="移除成員的身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_rem(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    try:
        await 成員.remove_roles(身分組)
        await interaction.response.send_message(f"已從 {成員.display_name} 移除 {身分組.name}")
    except Exception as e: await interaction.response.send_message(f"失敗: {e}")

@tree.command(name="查看審核日誌", description="查看操作紀錄")
@app_commands.describe(筆數="顯示數量(1-20)")
@app_commands.checks.has_permissions(view_audit_log=True)
async def show_logs(interaction: discord.Interaction, 筆數: int = 5):
    await interaction.response.defer(thinking=True)
    筆數 = min(max(筆數, 1), 20)
    log_text = f"### 最近的 {筆數} 筆審核日誌\n"
    async for entry in interaction.guild.audit_logs(limit=筆數):
        raw_action = str(entry.action).split('.')[-1]
        action_cn = AUDIT_LOG_ACTIONS_CN.get(raw_action, raw_action)
        log_text += f"* 時間: {entry.created_at.strftime('%Y-%m-%d %H:%M:%S')} | 執行者: {entry.user} | 動作: {action_cn} | 目標: {entry.target}\n"
    await interaction.followup.send(log_text)

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
