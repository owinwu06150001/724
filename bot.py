import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import datetime
import psutil
import static_ffmpeg
from server import keep_alive
import re
import json

# ===== 1. JSON 存檔邏輯 (確保重啟不失憶) =====
DATA_FILE = "config.json"

def load_config():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if "log_system_channels" not in data: data["log_system_channels"] = {}
                return data
            except: pass
    return {"stay_channels": {}, "stats_channels": {}, "log_system_channels": {}, "filter_config": None}

def save_config():
    data = {
        "stay_channels": stay_channels,
        "stats_channels": stats_channels,
        "log_system_channels": log_system_channels,
        "filter_config": filter_config
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def parse_duration(time_str: str) -> int:
    pattern = r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?"
    match = re.fullmatch(pattern, time_str.lower())
    if not match: return None
    days, hours, minutes, seconds = match.groups()
    return (int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0))

# 初始化 FFMPEG 與 Web 服務
static_ffmpeg.add_paths()
keep_alive()

# ===== 2. Intents 設定 =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 3. 全域資料與詞庫 =====
COMMON_PROFANITY = [
    "幹", "靠", "屁", "垃圾", "智障", "腦癱", "死全家", "孤兒", 
    "廢物", "去死", "操你媽", "你媽死了", "尼哥", "畜生", "雜種", 
    "低能兒", "白癡", "腦殘", "傻逼", "機掰", "雞掰", "賤人", "賤貨",
    "操", "肏", "幹你娘", "靠北", "靠腰", "三小", "幹林娘", "機歪",
    "支那", "下流", "無恥", "欠幹", "狗娘養的", "尼瑪"
]

_loaded = load_config()
stay_channels = _loaded.get("stay_channels", {})
stats_channels = _loaded.get("stats_channels", {})
log_system_channels = _loaded.get("log_system_channels", {})
filter_config = _loaded.get("filter_config") or {
    "enabled": False, "log_channel_id": None, "keywords": COMMON_PROFANITY
}

stay_since = {}
tag_targets = {}
queues = {} 

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

# ===== 4. 公用函數 =====
async def send_server_log(guild, embed):
    log_cid = log_system_channels.get(str(guild.id))
    if log_cid:
        channel = bot.get_channel(log_cid)
        if channel:
            try: await channel.send(embed=embed)
            except: pass

def get_help_text(bot_mention):
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 24/7 語音掛機設計 具備30秒自動重連機制。\n\n"
        "### 指令列表\n"
        "* /加入 [頻道]：進入語音頻道掛機。\n"
        "* /設定日誌頻道：設定顯示伺服器所有動態紀錄的頻道。\n"
        "* /設定統計頻道：建立自動更新人數的統計頻道。\n"
        "* /播放 [檔案]：上傳音檔播放。\n"
        "* /系統狀態：查看硬體資訊。\n"
        "* /開始標註 [成員] [內容] [次數]：執行標註轟炸。\n"
        "* /停止標註：結束轟炸。\n"
        "* /設定過濾器：開啟/關閉不雅語言禁言系統。\n"
        "* /狀態：查看掛機時間與延遲。\n"
        "* /查看審核日誌：查看操作紀錄。\n"
        "* /使用方式：顯示本手冊。"
    )

# =========================================================
# ===== 核心邏輯 (轟炸與音樂管理) =====
# =========================================================

async def tag_logic(channel, target, content, times):
    for i in range(times):
        if tag_targets.get(target.id) is False: break
        try: await channel.send(f"{target.mention} {content}")
        except: break
        await asyncio.sleep(0.8)

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

    @discord.ui.button(label="停止", style=discord.ButtonStyle.danger, row=0)
    async def stop_music(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.vc: self.manager.vc.stop()
        self.manager.queue.clear()
        self.manager.current = None
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

# =========================================================
# ===== 事件監聽 (紀錄、歡迎、過濾) =====
# =========================================================

@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user.mentioned_in(message) and message.mention_everyone is False:
        await message.channel.send(get_help_text(bot.user.mention))
    if filter_config["enabled"]:
        if any(word in message.content for word in filter_config["keywords"]):
            try:
                txt, user = message.content, message.author
                await message.delete()
                await user.timeout(datetime.timedelta(seconds=60), reason="使用不雅詞彙")
                if filter_config["log_channel_id"]:
                    log_ch = bot.get_channel(filter_config["log_channel_id"])
                    if log_ch:
                        e = discord.Embed(title="違規紀錄", color=0xff0000)
                        e.add_field(name="用戶", value=user.mention)
                        e.add_field(name="內容", value=txt)
                        await log_ch.send(embed=e)
            except: pass
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    # 歡迎卡片
    channel = member.guild.system_channel
    count = member.guild.member_count
    if channel:
        embed = discord.Embed(description=f"你好 歡迎加入 {member.guild.name}\n\n{member.mention}\n\n你是本伺服器的第 {count} 位成員", color=0x2b2d31)
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)
    # 動態日誌
    log_embed = discord.Embed(title="成員加入紀錄", color=0x2ecc71, timestamp=datetime.datetime.now())
    log_embed.add_field(name="成員", value=f"{member.mention} ({member.name})")
    log_embed.add_field(name="伺服器人數", value=f"第 {count} 位成員")
    await send_server_log(member.guild, log_embed)

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    embed = discord.Embed(title="訊息刪除紀錄", color=0xffa500, timestamp=datetime.datetime.now())
    embed.add_field(name="作者", value=message.author.mention)
    embed.add_field(name="頻道", value=message.channel.mention)
    embed.add_field(name="內容", value=message.content or "無文字內容", inline=False)
    await send_server_log(message.guild, embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content: return
    embed = discord.Embed(title="訊息編輯紀錄", color=0x3498db, timestamp=datetime.datetime.now())
    embed.add_field(name="作者", value=before.author.mention)
    embed.add_field(name="修改前", value=before.content, inline=False)
    embed.add_field(name="修改後", value=after.content, inline=False)
    await send_server_log(before.guild, embed)

@bot.event
async def on_member_remove(member):
    embed = discord.Embed(title="成員離開紀錄", color=0xe74c3c, timestamp=datetime.datetime.now())
    embed.add_field(name="成員名稱", value=f"{member.name} ({member.id})")
    await send_server_log(member.guild, embed)

# =========================================================
# ===== 指令區 (補完所有功能) =====
# =========================================================

@tree.command(name="設定日誌頻道", description="設定顯示伺服器變動紀錄的頻道")
@app_commands.checks.has_permissions(manage_guild=True)
async def set_log(interaction: discord.Interaction, 頻道: discord.TextChannel):
    log_system_channels[str(interaction.guild.id)] = 頻道.id
    save_config()
    await interaction.response.send_message(f"日誌頻道已設定為：{頻道.mention}")

@tree.command(name="加入", description="進入語音掛機")
async def join_vc(interaction: discord.Interaction, 頻道: discord.VoiceChannel = None):
    try:
        頻道 = 頻道 or (interaction.user.voice.channel if interaction.user.voice else None)
        if not 頻道:
            return await interaction.response.send_message("請指定頻道", ephemeral=True)

        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(頻道)
        else:
            await 頻道.connect(self_deaf=True)

        stay_channels[str(interaction.guild.id)] = 頻道.id
        stay_since[interaction.guild.id] = time.time()
        save_config()

        await interaction.response.send_message(f"我進來 {頻道.name} 竊聽了")

    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
        else:
            await interaction.response.send_message(f"發生錯誤：{e}", ephemeral=True)

@tree.command(name="播放", description="播放音檔")
async def play_audio(interaction: discord.Interaction, 檔案: discord.Attachment):
    if not 檔案.filename.endswith(('.mp3', '.ogg', '.m4a')): return await interaction.response.send_message("格式不支援", ephemeral=True)
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

@tree.command(name="開始標註", description="執行轟炸")
async def start_bomb(interaction: discord.Interaction, 成員: discord.Member, 內容: str, 次數: int):
    if 次數 <= 0: return await interaction.response.send_message("次數無效")
    tag_targets[成員.id] = True
    await interaction.response.send_message(f"開始轟炸 {成員.mention}")
    await tag_logic(interaction.channel, 成員, 內容, 次數)
    tag_targets[成員.id] = False

@tree.command(name="停止標註", description="停止轟炸")
async def stop_bomb(interaction: discord.Interaction, 成員: discord.Member):
    tag_targets[成員.id] = False
    await interaction.response.send_message(f"已停止對 {成員.mention} 的標註")

@tree.command(name="踢出", description="踢出成員")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, 成員: discord.Member, 原因: str = "無"):
    if 成員.top_role >= interaction.user.top_role: return await interaction.response.send_message("權限不足", ephemeral=True)
    await 成員.kick(reason=原因)
    await interaction.response.send_message(f"已踢出 {成員.mention}")

@tree.command(name="封鎖", description="封鎖成員")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, 成員: discord.Member, 原因: str = "無"):
    await 成員.ban(reason=原因)
    await interaction.response.send_message(f"已封鎖 {成員.mention}")

@tree.command(name="禁言", description="禁言成員")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, 成員: discord.Member, 天: int = 0, 小時: int = 0, 分鐘: int = 0, 秒數: int = 0, 原因: str = "無"):
    total = (天 * 86400 + 小時 * 3600 + 分鐘 * 60 + 秒數)
    if total <= 0 or total > 2419200: return await interaction.response.send_message("時間無效")
    await 成員.timeout(datetime.timedelta(seconds=total), reason=原因)
    await interaction.response.send_message(f"已禁言 {成員.mention}")

@tree.command(name="給予身分組", description="賦予身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_add(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    await 成員.add_roles(身分組)
    await interaction.response.send_message(f"已賦予 {身分組.name}")

@tree.command(name="移除身分組", description="移除身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_rem(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    await 成員.remove_roles(身分組)
    await interaction.response.send_message(f"已移除 {身分組.name}")

@tree.command(name="設定統計頻道", description="建立統計頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def stats_setup(interaction: discord.Interaction):
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True), guild.me: discord.PermissionOverwrite(manage_channels=True)}
    cat = await guild.create_category("伺服器數據", overwrites=overwrites)
    c1 = await guild.create_voice_channel(f"全部人數: {guild.member_count}", category=cat)
    stats_channels[str(guild.id)] = {"total": c1.id}
    save_config()
    await interaction.response.send_message("統計頻道建立完成")

@tree.command(name="系統狀態", description="查看硬體")
async def sys_info(interaction: discord.Interaction):
    await interaction.response.send_message(f"CPU: {psutil.cpu_percent()}% | RAM: {psutil.virtual_memory().percent}%")

@tree.command(name="狀態", description="查看掛機")
async def status_info(interaction: discord.Interaction):
    uptime = int(time.time() - stay_since.get(interaction.guild_id, time.time()))
    await interaction.response.send_message(f"掛機時間: {uptime} 秒 | 延遲: {round(bot.latency * 1000)} ms")

@tree.command(name="查看審核日誌", description="查看操作紀錄")
@app_commands.checks.has_permissions(view_audit_log=True)
async def show_logs(interaction: discord.Interaction, 筆數: int = 5):
    await interaction.response.defer()
    log_text = "### 最近審核日誌\n"
    async for entry in interaction.guild.audit_logs(limit=筆數):
        raw = str(entry.action).split('.')[-1]
        action = AUDIT_LOG_ACTIONS_CN.get(raw, raw)
        log_text += f"* 執行者: {entry.user} | 動作: {action} | 目標: {entry.target}\n"
    await interaction.followup.send(log_text)

@tree.command(name="使用方式", description="手冊")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(get_help_text(bot.user.mention))

@tree.command(name="離開", description="退出")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(str(interaction.guild.id), None)
        save_config()
        await interaction.response.send_message("我走了")
    else: await interaction.response.send_message("沒在語音中", ephemeral=True)

# =========================================================
# ===== 啟動與循環任務 =====
# =========================================================

@bot.event
async def on_ready():
    await tree.sync()
    check_connection.start()
    update_member_stats.start()
    for gid_str, cid in list(stay_channels.items()):
        guild = bot.get_guild(int(gid_str))
        if guild and not guild.voice_client:
            ch = bot.get_channel(cid)
            if ch: 
                try: await ch.connect(self_deaf=True); stay_since[int(gid_str)] = time.time()
                except: pass
    print(f"機器人已啟動：{bot.user}")

@tasks.loop(seconds=30)
async def check_connection():
    for gid_str, cid in list(stay_channels.items()):
        guild = bot.get_guild(int(gid_str))
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        ch = bot.get_channel(cid)
        if ch: 
            try: await ch.connect(self_deaf=True)
            except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for gid_str, stats in list(stats_channels.items()):
        guild = bot.get_guild(int(gid_str))
        if guild:
            ch = bot.get_channel(stats.get("total"))
            if ch: 
                try: await ch.edit(name=f"全部人數: {guild.member_count}")
                except: pass

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)

