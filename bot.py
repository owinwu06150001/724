import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import aiohttp
import psutil
import datetime
import static_ffmpeg
from server import keep_alive

# 初始化 FFMPEG
static_ffmpeg.add_paths()

# ===== 啟動 Web 服務 =====
keep_alive()

# ===== Intents 設定 =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 資料儲存 =====
stay_channels = {}
stay_since = {}
tag_targets = {}
stats_channels = {}
queues = {} 

# 不雅語言偵測設定
filter_config = {
    "enabled": False,
    "log_channel_id": None,
    "keywords": ["髒話1", "髒話2"]
}

# ===== 播放音檔設定 =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# ===== 審核日誌對照表 =====
AUDIT_LOG_ACTIONS_CN = {
    "guild_update": "更新伺服器", "channel_create": "建立頻道", "channel_update": "更新頻道",
    "channel_delete": "刪除頻道", "member_kick": "踢出成員", "member_ban": "封鎖成員",
    "member_unban": "解除封鎖", "member_update": "更新成員", "member_role_update": "更新成員身分組",
    "role_create": "建立身分組", "role_update": "更新身分組", "role_delete": "刪除身分組",
    "message_delete": "刪除訊息", "message_bulk_delete": "批量刪除訊息",
}

# ===== 公用手冊內容 (嚴格遵守原台詞，無表情符號) =====
def get_help_text(bot_mention):
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 24/7 語音掛機 設計 具備30秒自動重連機制。\n\n"
        "### 指令列表\n"
        "* /加入 [頻道]：讓機器人進入語音頻道（可不選，預設進入你所在的頻道）。\n"
        "* /設定統計頻道：建立自動更新人數的統計頻道。\n"
        "* /播放 [檔案]：直接上傳 mp3, ogg, m4a 檔案進行播放。\n"
        "* /系統狀態：查看硬體資訊。\n"
        "* /停止播放：停止目前播放的音檔。\n"
        "* /離開：讓機器人退出語音頻道並停止掛機。\n"
        "* /開始標註 [成員] [內容] [次數]：瘋狂轟炸某人。\n"
        "* /停止標註：結束目前的轟炸。\n"
        "* /狀態：查看目前掛機頻道、已掛機時間與延遲。\n"
        "* /移除身分組 [成員] [身分組]：將某人的身分組拔掉。\n"
        "* /給予身分組 [成員] [身分組]：給予某人特定的身分組。\n"
        "* /查看審核日誌 [筆數]：查看伺服器最近的操作紀錄。\n"
        "* /使用方式：顯示此幫助選單。"
    )

# =========================================================
# ===== 轟炸核心邏輯 =====
# =========================================================
async def tag_logic(channel, target, content, times):
    for i in range(times):
        # 如果中途被停止，則跳出迴圈
        if tag_targets.get(target.id) is False:
            break
        await channel.send(f"{target.mention} {content}")
        await asyncio.sleep(0.8)

# =========================================================
# ===== 音樂管理系統 =====
# =========================================================
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
        if self.manager.vc.is_playing(): self.manager.vc.pause()
        elif self.manager.vc.is_paused(): self.manager.vc.resume()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

# =========================================================
# ===== 機器人事件 (含標註機器人顯示手冊) =====
# =========================================================
@bot.event
async def on_message(message):
    if message.author.bot: return

    # 標註機器人顯示手冊台詞
    if bot.user.mentioned_in(message) and message.mention_everyone is False:
        await message.channel.send(get_help_text(bot.user.mention))

    # 不雅語言偵測禁言
    if filter_config["enabled"]:
        if any(word in message.content for word in filter_config["keywords"]):
            try:
                msg_text = message.content
                user = message.author
                await message.delete()
                await user.timeout(datetime.timedelta(seconds=60), reason="使用不雅詞彙")
                
                if filter_config["log_channel_id"]:
                    log_ch = bot.get_channel(filter_config["log_channel_id"])
                    if log_ch:
                        log_embed = discord.Embed(title="不雅言論紀錄", color=0xff0000)
                        log_embed.add_field(name="用戶", value=user.mention)
                        log_embed.add_field(name="內容", value=msg_text)
                        await log_ch.send(embed=log_embed)
            except: pass

    await bot.process_commands(message)

@bot.event
async def on_ready():
    await tree.sync()
    update_member_stats.start()
    check_connection.start()
    print(f"機器人已上線：{bot.user}")

# =========================================================
# ===== 指令區 (全中文指令) =====
# =========================================================

@tree.command(name="使用方式", description="顯示指令手冊")
async def show_help(interaction: discord.Interaction):
    await interaction.response.send_message(get_help_text(bot.user.mention))

@tree.command(name="開始標註", description="對指定成員進行標註轟炸")
@app_commands.describe(成員="轟炸對象", 內容="發送的訊息內容", 次數="要標註的總次數")
async def start_bomb(interaction: discord.Interaction, 成員: discord.Member, 內容: str, 次數: int):
    if 次數 <= 0: return await interaction.response.send_message("次數必須大於 0", ephemeral=True)
    tag_targets[成員.id] = True
    await interaction.response.send_message(f"開始對 {成員.mention} 執行 {次數} 次標註")
    await tag_logic(interaction.channel, 成員, 內容, 次數)
    tag_targets[成員.id] = False

@tree.command(name="停止標註", description="中斷對該成員的轟炸")
@app_commands.describe(成員="停止標註的對象")
async def stop_bomb(interaction: discord.Interaction, 成員: discord.Member):
    tag_targets[成員.id] = False
    await interaction.response.send_message(f"已嘗試停止對 {成員.mention} 的標註")

@tree.command(name="設定過濾器", description="開啟或關閉不雅語言禁言系統")
@app_commands.describe(開啟="開啟或關閉", 記錄頻道="日誌發送頻道")
@app_commands.checks.has_permissions(manage_guild=True)
async def filter_set(interaction: discord.Interaction, 開啟: bool, 記錄頻道: discord.TextChannel):
    filter_config["enabled"] = 開啟
    filter_config["log_channel_id"] = 記錄頻道.id
    status = "開啟" if 開啟 else "關閉"
    await interaction.response.send_message(f"過濾系統已{status} 日誌頻道: {記錄頻道.mention}")

@tree.command(name="給予身分組", description="賦予成員身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_add(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    try:
        await 成員.add_roles(身分組)
        await interaction.response.send_message(f"已將 {身分組.name} 給予 {成員.display_name}")
    except Exception as e:
        await interaction.response.send_message(f"失敗: {e}")

@tree.command(name="移除身分組", description="移除成員的身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_rem(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    try:
        await 成員.remove_roles(身分組)
        await interaction.response.send_message(f"已從 {成員.display_name} 移除 {身分組.name}")
    except Exception as e:
        await interaction.response.send_message(f"失敗: {e}")

@tree.command(name="停止播放", description="中斷目前的音檔播放")
async def stop_audio(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("播放已停止")
    else: await interaction.response.send_message("目前沒有正在播放的音檔")

@tree.command(name="系統狀態", description="查看當前硬體使用率")
async def sys_info(interaction: discord.Interaction):
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    await interaction.response.send_message(f"CPU 使用率: {cpu}% | 記憶體使用率: {ram}%")

@tree.command(name="查看審核日誌", description="查看伺服器操作紀錄")
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

@tree.command(name="加入", description="進入語音頻道開啟掛機")
async def join_vc(interaction: discord.Interaction, 頻道: discord.VoiceChannel = None):
    頻道 = 頻道 or getattr(interaction.user.voice, 'channel', None)
    if not 頻道: return await interaction.response.send_message("未找到語音頻道", ephemeral=True)
    await 頻道.connect(self_deaf=True)
    stay_channels[interaction.guild.id] = 頻道.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.response.send_message(f"已進入頻道: {頻道.name}")

@tree.command(name="離開", description="離開語音頻道並中斷掛機")
async def leave_vc(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        await interaction.response.send_message("已退出頻道")
    else: await interaction.response.send_message("機器人不在語音頻道中")

@tree.command(name="播放", description="播放上傳的音檔")
async def play_audio(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer(thinking=True)
    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]
    if not interaction.guild.voice_client:
        if not interaction.user.voice: return await interaction.followup.send("請先進入語音")
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True)
        stay_channels[gid] = interaction.user.voice.channel.id
        stay_since[gid] = time.time()
    else: mgr.vc = interaction.guild.voice_client
    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing() and not mgr.vc.is_paused(): mgr.play_next()
    await interaction.followup.send(embed=mgr.get_status_embed())

@tree.command(name="設定統計頻道", description="建立人數統計頻道")
async def stats_setup(interaction: discord.Interaction):
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False), guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True)}
    category = await guild.create_category("伺服器數據", position=0)
    c_total = await guild.create_voice_channel(f"全部人數: {guild.member_count}", category=category, overwrites=overwrites)
    stats_channels[guild.id] = {"total": c_total.id}
    await interaction.response.send_message("統計頻道建立完成")

@tree.command(name="狀態", description="查看掛機時間與延遲")
async def status_info(interaction: discord.Interaction):
    if interaction.guild_id not in stay_channels: return await interaction.response.send_message("未在掛機狀態", ephemeral=True)
    uptime = int(time.time() - stay_since.get(interaction.guild_id, time.time()))
    await interaction.response.send_message(f"掛機時間: {uptime} 秒 | 延遲: {round(bot.latency * 1000)} ms")

# ===== 背景任務邏輯 =====
@tasks.loop(seconds=30)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        ch = bot.get_channel(cid)
        if ch: 
            try: await ch.connect(self_deaf=True)
            except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds:
        if guild.id in stats_channels:
            ch = bot.get_channel(stats_channels[guild.id]["total"])
            if ch: await ch.edit(name=f"全部人數: {guild.member_count}")

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
