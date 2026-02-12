import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import aiohttp
import psutil
import static_ffmpeg
import requests
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

# =========================================================
# ===== 審核日誌中文翻譯對照表 =====
# =========================================================
AUDIT_LOG_ACTIONS_CN = {
    "guild_update": "更新伺服器",
    "channel_create": "建立頻道",
    "channel_update": "更新頻道",
    "channel_delete": "刪除頻道",
    "member_kick": "踢出成員",
    "member_prune": "清理成員",
    "member_ban": "封鎖成員",
    "member_unban": "解除封鎖",
    "member_update": "更新成員",
    "member_role_update": "更新成員身分組",
    "member_move": "移動成員",
    "member_disconnect": "中斷成員連線",
    "bot_add": "添加機器人",
    "role_create": "建立身分組",
    "role_update": "更新身分組",
    "role_delete": "刪除身分組",
    "invite_create": "建立邀請",
    "invite_update": "更新邀請",
    "invite_delete": "刪除邀請",
    "webhook_create": "建立 Webhook",
    "webhook_update": "更新 Webhook",
    "webhook_delete": "刪除 Webhook",
    "emoji_create": "建立表情符號",
    "emoji_update": "更新表情符號",
    "emoji_delete": "刪除表情符號",
    "message_delete": "刪除訊息",
    "message_bulk_delete": "批量刪除訊息",
    "message_pin": "釘選訊息",
    "message_unpin": "取消釘選訊息",
    "thread_create": "建立討論串",
    "thread_update": "更新討論串",
    "thread_delete": "刪除討論串",
}

# =========================================================
# ===== 音樂管理系統 Class =====
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
        loop_map = {"none": "關閉", "single": "單曲", "all": "全清單"}
        embed = discord.Embed(title="音樂控制面板", color=0xaa96da)
        embed.add_field(name="當前歌曲", value=self.current[1] if self.current else "無", inline=False)
        embed.add_field(name="狀態", value=status, inline=True)
        embed.add_field(name="循環模式", value=loop_map.get(self.mode, "關閉"), inline=True)
        embed.add_field(name="當前音量", value=f"{int(self.volume*100)}%", inline=True)
        q_len = len(self.queue)
        embed.set_footer(text=f"待播清單剩餘: {q_len} 首歌曲")
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

    @discord.ui.button(label="上一首", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.history: return await interaction.response.send_message("沒有歷史紀錄", ephemeral=True)
        last = self.manager.history.pop()
        if self.manager.current: self.manager.queue.insert(0, self.manager.current)
        self.manager.queue.insert(0, last)
        self.manager.vc.stop()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.vc.is_playing(): self.manager.vc.pause()
        elif self.manager.vc.is_paused(): self.manager.vc.resume()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="下一首", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue: return await interaction.response.send_message("待播清單已空", ephemeral=True)
        self.manager.vc.stop()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

# =========================================================
# ===== 工具函式與統計邏輯 =====
# =========================================================
async def get_system_info():
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get('https://api.ipify.org', timeout=5) as resp: ip = await resp.text()
        except: ip = "無法獲取"
    return {"cpu": f"{cpu_usage}%", "ram": f"{round(ram.used/(1024**3),2)}GB/{round(ram.total/(1024**3),2)}GB", "ip": ip}

def format_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60); h, m = divmod(m, 60); d, h = divmod(h, 24)
    return f"{d}天 {h}時 {m}分 {s}秒"

async def update_stats_logic(guild):
    if guild.id not in stats_channels: return
    channels = stats_channels[guild.id]
    total = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    online = sum(1 for m in guild.members if not m.bot and m.status != discord.Status.offline)
    mapping = {"total": f"全部人數: {total}", "members": f"成員人數: {total - bots}", "online": f"在線成員: {online}", "bots": f"機器人: {bots}"}
    for key, name in mapping.items():
        if key in channels:
            ch = bot.get_channel(channels[key])
            if ch and ch.name != name:
                try: await ch.edit(name=name)
                except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds: await update_stats_logic(guild)

# =========================================================
# ===== BOT 事件 =====
# =========================================================
@bot.event
async def on_ready():
    await tree.sync()
    print(f"機器人已上線：{bot.user}")
    update_member_stats.start()
    check_connection.start()
    tagging_task.start()

@bot.event
async def on_presence_update(before, after):
    await update_stats_logic(after.guild)

@bot.event
async def on_member_join(member):
    await update_stats_logic(member.guild)

@bot.event
async def on_member_remove(member):
    await update_stats_logic(member.guild)

@tasks.loop(seconds=30)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        ch = bot.get_channel(cid)
        if ch: 
            try: await ch.connect(self_deaf=True)
            except: pass

@tasks.loop(seconds=1.5)
async def tagging_task():
    for gid, data in list(tag_targets.items()):
        ch = bot.get_channel(data["channel_id"])
        if not ch: continue
        try:
            await ch.send(f"<@{data['user_id']}> {data['content']}")
            if data["count"] is not None:
                data["count"] -= 1
                if data["count"] <= 0: tag_targets.pop(gid)
        except: pass

# =========================================================
# ===== Slash Commands =====
# =========================================================

@tree.command(name="查看審核日誌", description="查看伺服器最近的操作紀錄 (中文顯示)")
@app_commands.describe(limit="要查看的筆數 (1-20)")
@app_commands.checks.has_permissions(view_audit_log=True)
async def view_audit_log(interaction: discord.Interaction, limit: int = 5):
    await interaction.response.defer(thinking=True)
    limit = min(max(limit, 1), 20)
    log_text = f"### 最近的 {limit} 筆審核日誌\n"
    
    async for entry in interaction.guild.audit_log(limit=limit):
        created_at = entry.created_at.strftime("%Y-%m-%d %H:%M:%S")
        # 取得英文動作名稱並轉換中文
        raw_action = str(entry.action).split('.')[-1]
        action_cn = AUDIT_LOG_ACTIONS_CN.get(raw_action, raw_action)
        
        log_text += f"* **時間**: `{created_at}` | **執行者**: **{entry.user}** | **動作**: **{action_cn}** | **目標**: {entry.target}\n"
    
    await interaction.followup.send(log_text)

@tree.command(name="給予身分組", description="給予指定成員身分組")
async def add_role(interaction: discord.Interaction, target: discord.Member, role: discord.Role):
    await interaction.response.defer(thinking=True)
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.followup.send("錯誤：你沒有權限。", ephemeral=True)
    try:
        await target.add_roles(role)
        await interaction.followup.send(f"成功給予 **{target.display_name}** **{role.name}** 身分組。")
    except Exception as e:
        await interaction.followup.send(f"失敗: {e}", ephemeral=True)

@tree.command(name="移除身分組", description="移除指定成員的身分組")
async def remove_role(interaction: discord.Interaction, target: discord.Member, role: discord.Role):
    await interaction.response.defer(thinking=True)
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.followup.send("錯誤：你沒有權限。", ephemeral=True)
    try:
        await target.remove_roles(role)
        await interaction.followup.send(f"成功移除 **{target.display_name}** 的 **{role.name}** 身分組。")
    except Exception as e:
        await interaction.followup.send(f"失敗: {e}", ephemeral=True)

@tree.command(name="設定統計頻道", description="建立顯示伺服器人數與在線人數的統計頻道")
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

@tree.command(name="加入", description="機器人進入語音頻道")
async def join(interaction: discord.Interaction, channel: discord.VoiceChannel | None = None):
    await interaction.response.defer(thinking=True)
    channel = channel or getattr(interaction.user.voice, 'channel', None)
    if not channel: return await interaction.followup.send("未找到語音頻道", ephemeral=True)
    await channel.connect(self_deaf=True)
    stay_channels[interaction.guild.id] = channel.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.followup.send(f"進入: {channel.name}")

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

@tree.command(name="系統狀態", description="查看硬體資訊")
async def system_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    info = await get_system_info()
    embed = discord.Embed(title="伺服器狀態", color=0x3498db)
    embed.add_field(name="CPU", value=info["cpu"], inline=True)
    embed.add_field(name="RAM", value=info["ram"], inline=True)
    embed.add_field(name="IP", value=info["ip"], inline=False)
    await interaction.followup.send(embed=embed)

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
