import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import aiohttp
import psutil
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

# =========================================================
# ===== 音樂管理系統 (含單曲/歌單循環) =====
# =========================================================
class MusicManager:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = []     
        self.history = []    
        self.current = None  
        self.volume = 0.5    
        self.mode = "none" # none: 不循環, single: 單曲循環, all: 歌單循環
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
            if self.mode == "single":
                self.queue.insert(0, self.current)
            elif self.mode == "all":
                self.queue.append(self.current)
            else:
                self.history.append(self.current)
        
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

    @discord.ui.button(label="下一首", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.vc.stop()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="切換循環", style=discord.ButtonStyle.success, row=0)
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        modes = ["none", "single", "all"]
        self.manager.mode = modes[(modes.index(self.manager.mode) + 1) % 3]
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="音量 +", style=discord.ButtonStyle.gray, row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = min(self.manager.volume + 0.1, 2.0)
        if self.manager.vc and self.manager.vc.source:
            self.manager.vc.source.volume = self.manager.volume
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="音量 -", style=discord.ButtonStyle.gray, row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = max(self.manager.volume - 0.1, 0.0)
        if self.manager.vc and self.manager.vc.source:
            self.manager.vc.source.volume = self.manager.volume
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

# =========================================================
# ===== 機器人事件 (含標註機器人顯示使用方式) =====
# =========================================================
@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user.mentioned_in(message) and message.mention_everyone is False:
        help_text = (
            "### 🎵 機器人指令使用說明\n"
            "本機器人支援 Slash 指令 (輸入 `/` 即可看到選項)：\n"
            "- `/播放`: 上傳音檔進行播放，並開啟控制面板 (含音量、循環切換)\n"
            "- `/加入`: 讓機器人進入你所在的語音頻道\n"
            "- `/離開`: 讓機器人離開頻道並重設狀態\n"
            "- `/查看審核日誌`: 以中文顯示伺服器最近的操作紀錄\n"
            "- `/設定統計頻道`: 自動建立伺服器人數統計\n"
            "- `/系統狀態`: 查看目前伺服器的 CPU 與 RAM 資訊\n"
            "- `/狀態`: 檢查機器人掛機時間與延遲"
        )
        await message.channel.send(help_text)
    await bot.process_commands(message)

@bot.event
async def on_ready():
    await tree.sync()
    print(f"機器人已上線：{bot.user}")
    update_member_stats.start()
    check_connection.start()

# =========================================================
# ===== 背景任務與指令區 =====
# =========================================================
async def update_stats_logic(guild):
    if guild.id not in stats_channels: return
    ch_data = stats_channels[guild.id]
    total = guild.member_count
    online = sum(1 for m in guild.members if not m.bot and m.status != discord.Status.offline)
    mapping = {"total": f"全部人數: {total}", "online": f"在線成員: {online}"}
    for key, name in mapping.items():
        if key in ch_data:
            ch = bot.get_channel(ch_data[key])
            if ch:
                try: await ch.edit(name=name)
                except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds: await update_stats_logic(guild)

@tasks.loop(seconds=30)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        ch = bot.get_channel(cid)
        if ch: 
            try: await ch.connect(self_deaf=True)
            except: pass

@tree.command(name="查看審核日誌", description="查看伺服器最近的操作紀錄 (中文顯示)")
@app_commands.describe(limit="筆數 (1-20)")
@app_commands.checks.has_permissions(view_audit_log=True)
async def view_audit_log(interaction: discord.Interaction, limit: int = 5):
    await interaction.response.defer(thinking=True)
    limit = min(max(limit, 1), 20)
    log_text = f"### 最近的 {limit} 筆審核日誌\n"
    try:
        async for entry in interaction.guild.audit_logs(limit=limit):
            raw_action = str(entry.action).split('.')[-1]
            action_cn = AUDIT_LOG_ACTIONS_CN.get(raw_action, raw_action)
            log_text += f"* **時間**: `{entry.created_at.strftime('%Y-%m-%d %H:%M:%S')}` | **執行者**: **{entry.user}** | **動作**: **{action_cn}** | **目標**: {entry.target}\n"
    except Exception as e: log_text = f"獲取失敗: {e}"
    await interaction.followup.send(log_text)

@tree.command(name="加入", description="機器人進入語音頻道")
async def join(interaction: discord.Interaction, channel: discord.VoiceChannel | None = None):
    await interaction.response.defer(thinking=True)
    channel = channel or getattr(interaction.user.voice, 'channel', None)
    if not channel: return await interaction.followup.send("未找到語音頻道", ephemeral=True)
    await channel.connect(self_deaf=True)
    stay_channels[interaction.guild.id] = channel.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.followup.send(f"我進來: {channel.name} 竊聽了")

@tree.command(name="離開", description="機器人離開語音頻道")
async def leave(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        stay_since.pop(interaction.guild.id, None)
        queues.pop(interaction.guild.id, None)
        await interaction.followup.send("我走了")
    else: await interaction.followup.send("機器人不在語音頻道中", ephemeral=True)

@tree.command(name="播放", description="上傳音檔播放")
async def play_file(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer(thinking=True)
    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]
    if not interaction.guild.voice_client:
        if not interaction.user.voice: return await interaction.followup.send("請先進入語音", ephemeral=True)
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True)
        stay_channels[gid] = interaction.user.voice.channel.id
        stay_since[gid] = time.time()
    else: mgr.vc = interaction.guild.voice_client
    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing() and not mgr.vc.is_paused(): mgr.play_next()
    await interaction.followup.send(embed=mgr.get_status_embed(), view=MusicControlView(mgr))

@tree.command(name="設定統計頻道", description="建立統計人數頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def setup_stats(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False), guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True)}
    category = await guild.create_category("伺服器數據", position=0)
    c_total = await guild.create_voice_channel(f"全部人數: {guild.member_count}", category=category, overwrites=overwrites)
    c_online = await guild.create_voice_channel("在線成員: 計算中...", category=category, overwrites=overwrites)
    stats_channels[guild.id] = {"total": c_total.id, "online": c_online.id}
    await update_stats_logic(guild)
    await interaction.followup.send("統計頻道建立完成")

@tree.command(name="系統狀態", description="查看硬體資訊")
async def system_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    embed = discord.Embed(title="伺服器硬體狀態", color=0x3498db)
    embed.add_field(name="CPU 使用率", value=f"{cpu_usage}%", inline=True)
    embed.add_field(name="記憶體使用", value=f"{round(ram.used/(1024**3),2)}GB/{round(ram.total/(1024**3),2)}GB", inline=True)
    
@tree.command(name="狀態", description="檢查掛機時間與延遲")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    if interaction.guild_id not in stay_channels: return await interaction.followup.send("機器人未在掛機狀態", ephemeral=True)
    uptime = int(time.time() - stay_since.get(interaction.guild_id, time.time()))
    await interaction.followup.send(f"目前掛機時間: {uptime} 秒\n延遲: {round(bot.latency * 1000)} ms", ephemeral=True)

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)

