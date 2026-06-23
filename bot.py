import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import datetime
import psutil
import static_ffmpeg
import server
import json

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
queues = {}
welcome_channels = {} 
filter_configs = {}

COMMON_PROFANITY = [
    "幹", "靠", "屁", "垃圾", "智障", "腦癱", "死全家", "孤兒", 
    "廢物", "去死", "操你媽", "你媽死了", "尼哥", "畜生", "雜種", 
    "低能兒", "白癡", "腦殘", "傻逼", "機掰", "雞掰", "賤人", "賤貨",
    "操", "肏", "幹你娘", "靠北", "靠腰", "三小", "幹林娘", "機歪",
    "支那", "下流", "無恥", "欠幹", "狗娘養的", "尼瑪"
]

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

AUDIT_LOG_ACTIONS_CN = {
    "guild_update": "更新伺服器", "channel_create": "建立頻道", "channel_update": "更新頻道",
    "channel_delete": "刪除頻道", "member_kick": "踢出成員", "member_ban": "封鎖成員",
    "member_unban": "解除封鎖", "member_update": "更新成員", "member_role_update": "更新成員身分組",
    "role_create": "建立身分組", "role_update": "更新身分組", "role_delete": "刪除身分組",
    "message_delete": "刪除訊息", "message_bulk_delete": "批量刪除訊息",
}

def get_help_text(bot_mention):
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 24/7 語音掛機設計 具備30秒自動重連機制。\n\n"
        "### 指令列表\n"
        "* /加入 [頻道]：進入語音頻道掛機。\n"
        "* /設定統計頻道：建立自動更新人數的統計頻道。\n"
        "* /播放 [檔案]：上傳音檔（mp3, ogg, m4a）播放。\n"
        "* /系統狀態：查看硬體資訊。\n"
        "* /停止播放：中斷目前的音樂。\n"
        "* /離開：退出頻道並停止掛機。\n"
        "* /開始標註 [成員] [內容] [次數]：執行標註轟炸。\n"
        "* /停止標註：結束轟炸。\n"
        "* /設定過濾器：開啟/關閉不雅語言禁言系統。\n"
        "* /新增過濾詞彙：手動加入關鍵字。\n"
        "* /狀態：查看掛機時間與延遲。\n"
        "* /移除身分組 / /給予身分組：管理成員權限。\n"
        "* /建立身分組面板 [身分組] [圖片網址]：發送按鈕面板。\n"
        "* /設定歡迎頻道 [頻道]：設定歡迎訊息發送位置。\n"
        "* /查看審核日誌：查看操作紀錄。\n"
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

class MusicManager:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = []
        self.history = []
        self.current = None
        self.volume = 0.5
        self.mode = "none"
        self.vc = None
    def get_status_embed(self):
        status = "播放中" if self.vc and self.vc.is_playing() else "已暫停"
        loop_map = {"none": "不循環", "single": "單曲循環", "all": "歌單循環"}
        embed = discord.Embed(title="音樂控制面板", color=0xaa96da)
        embed.add_field(name="當前歌曲", value=self.current[1] if self.current else "無", inline=False)
        embed.add_field(name="狀態", value=status, inline=True)
        embed.add_field(name="循環模式", value=loop_map.get(self.mode), inline=True)
        embed.add_field(name="當前音量", value=f"{int(self.volume*100)}%", inline=True)
        embed.set_footer(text=f"待播清單剩餘: {len(self.queue)} 首歌曲")
        return embed
    def play_next(self, error=None):
        if not self.vc or not self.vc.is_connected(): return
        if self.current:
            if self.mode == "single": self.queue.insert(0, self.current)
            elif self.mode == "all": self.queue.append(self.current)
            else: self.history.append(self.current)
        if not self.queue:
            self.current = None
            return
        self.current = self.queue.pop(0)
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(self.current[0], **FFMPEG_OPTIONS),
            volume=self.volume
        )
        self.vc.play(source, after=lambda e: bot.loop.call_soon_threadsafe(self.play_next, e))

class MusicControlView(discord.ui.View):
    def __init__(self, manager):
        super().__init__(timeout=None)
        self.manager = manager
    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.vc: return
        if self.manager.vc.is_playing(): self.manager.vc.pause()
        elif self.manager.vc.is_paused(): self.manager.vc.resume()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

async def tag_logic(channel, target, content, times):
    for i in range(times):
        if tag_targets.get(target.id) is False: break
        try: await channel.send(f"{target.mention} {content}")
        except: break
        await asyncio.sleep(0.8)

@tasks.loop(seconds=5)
async def process_broadcast_queue():
    queue = server.bot_status.get("broadcast_queue", [])
    if queue:
        item = queue.pop(0) # 取出任務
        channel = bot.get_channel(item["cid"])
        if channel:
            try:
                await channel.send(item["msg"])
                server.add_log(f"廣播發送成功: {item['msg'][:10]}...")
            except Exception as e:
                server.add_log(f"廣播失敗: {e}")
# 記得在 on_ready 中啟動它
@bot.event
async def on_ready():
    # ... 其他任務 ...
    if not process_broadcast_queue.is_running():
        process_broadcast_queue.start()

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
    config = filter_configs.get(message.guild.id, {"enabled": False, "keywords": COMMON_PROFANITY})
    if config.get("enabled"):
        if any(word in message.content for word in config.get("keywords")):
            try:
                msg_text = message.content
                user = message.author
                await message.delete()
                await user.timeout(datetime.timedelta(seconds=60), reason="使用不雅詞彙")
                log_cid = config.get("log_channel_id")
                if log_cid:
                    log_ch = bot.get_channel(log_cid)
                    if log_ch:
                        log_embed = discord.Embed(title="違規紀錄", color=0xff0000)
                        log_embed.add_field(name="用戶", value=user.mention)
                        log_embed.add_field(name="違規內容", value=msg_text)
                        await log_ch.send(embed=log_embed)
            except: pass
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    channel_id = welcome_channels.get(member.guild.id)
    if channel_id:
        channel = bot.get_channel(channel_id)
        if channel:
            embed = discord.Embed(title="歡迎訊息", description=f"你好 歡迎加入 {member.guild.name}！\n\n{member.mention}\n\n你是本伺服器的第 {member.guild.member_count} 位成員", color=0xaa96da)
            await channel.send(embed=embed)

@bot.event
async def on_ready():
    if not update_web_stats.is_running(): update_web_stats.start()
    if not check_connection.is_running(): check_connection.start()
    if not update_member_stats.is_running(): update_member_stats.start()
    if not process_broadcast_queue.is_running(): process_broadcast_queue.start()
    if not check_restart.is_running(): check_restart.start()
    await bot.tree.sync()
    print("機器人已啟動並連線至 Discord")

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

@tree.command(name="設定過濾器", description="開啟/關閉禁言系統")
@app_commands.describe(開啟="是否啟動", 記錄頻道="違規訊息日誌頻道")
@app_commands.checks.has_permissions(manage_guild=True)
async def filter_set(interaction: discord.Interaction, 開啟: bool, 記錄頻道: discord.TextChannel):
    filter_configs[interaction.guild.id] = {"enabled": 開啟, "log_channel_id": 記錄頻道.id, "keywords": COMMON_PROFANITY.copy()}
    await interaction.response.send_message(f"過濾系統：{'開啟' if 開啟 else '關閉'}，日誌頻道：{記錄頻道.mention}")

@tree.command(name="新增過濾詞彙", description="加入新的禁止字詞")
@app_commands.describe(詞彙="要禁用的字詞")
@app_commands.checks.has_permissions(manage_guild=True)
async def add_profanity(interaction: discord.Interaction, 詞彙: str):
    if interaction.guild.id not in filter_configs:
        filter_configs[interaction.guild.id] = {"enabled": False, "keywords": COMMON_PROFANITY.copy()}
    if 詞彙 not in filter_configs[interaction.guild.id]["keywords"]:
        filter_configs[interaction.guild.id]["keywords"].append(詞彙)
        await interaction.response.send_message(f"已將「{詞彙}」加入過濾名單")
    else: await interaction.response.send_message("該詞彙已在名單中")

@tree.command(name="開始標註", description="對成員執行轟炸")
async def start_bomb(interaction: discord.Interaction, 成員: discord.Member, 內容: str, 次數: int):
    if 次數 <= 0: return await interaction.response.send_message("次數必須大於0", ephemeral=True)
    tag_targets[成員.id] = True
    await interaction.response.send_message(f"開始轟炸 {成員.mention}")
    await tag_logic(interaction.channel, 成員, 內容, 次數)
    tag_targets[成員.id] = False

@tree.command(name="停止標註", description="停止轟炸")
async def stop_bomb(interaction: discord.Interaction, 成員: discord.Member):
    tag_targets[成員.id] = False
    await interaction.response.send_message(f"已停止對 {成員.mention} 的動作")

@tree.command(name="加入", description="進入語音頻道掛機")
async def join_vc(interaction: discord.Interaction, 頻道: discord.VoiceChannel = None):
    頻道 = 頻道 or (interaction.user.voice.channel if interaction.user.voice else None)
    if not 頻道: return await interaction.response.send_message("請先進入頻道或指定頻道", ephemeral=True)
    await 頻道.connect(self_deaf=True)
    stay_channels[interaction.guild.id] = 頻道.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.response.send_message(f"已連接至：{頻道.name}")

@tree.command(name="播放", description="播放上傳的音檔")
async def play_audio(interaction: discord.Interaction, 檔案: discord.Attachment):
    if not 檔案.filename.endswith(('.mp3', '.ogg', '.m4a')):
        return await interaction.response.send_message("格式不支援", ephemeral=True)
    await interaction.response.defer(thinking=True)
    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]
    if not interaction.guild.voice_client:
        if not interaction.user.voice: return await interaction.followup.send("請先進入語音")
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True)
    else: mgr.vc = interaction.guild.voice_client
    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing() and not mgr.vc.is_paused(): mgr.play_next()
    await interaction.followup.send(embed=mgr.get_status_embed(), view=MusicControlView(mgr))

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

@tree.command(name="系統狀態", description="硬體監控")
async def sys_info(interaction: discord.Interaction):
    await interaction.response.send_message(f"CPU: {psutil.cpu_percent()}% | RAM: {psutil.virtual_memory().percent}%")

@tree.command(name="離開", description="退出語音")
async def leave_vc(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        await interaction.response.send_message("已離開語音頻道")
    else: await interaction.response.send_message("目前不在語音中")

@tree.command(name="狀態", description="查看掛機時間與延遲")
async def status_info(interaction: discord.Interaction):
    if interaction.guild_id not in stay_channels: return await interaction.response.send_message("未在掛機狀態", ephemeral=True)
    uptime = int(time.time() - stay_since.get(interaction.guild_id, time.time()))
    await interaction.response.send_message(f"掛機時間: {uptime} 秒 | 延遲: {round(bot.latency * 1000)} ms")

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
