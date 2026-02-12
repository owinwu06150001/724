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

# 初始化 FFmpeg 路徑
static_ffmpeg.add_paths()

# ===== 啟動 Web 服務 (用於 24/7 監控) =====
keep_alive()

# ===== Intents 設定 =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True 

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 全域資料儲存 =====
stay_channels = {}
stay_since = {}
tag_targets = {}
stats_channels = {}
queues = {} 

# ===== 播放參數設定 =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# =========================================================
# ===== 音樂管理系統 (支援隊列與控制面板) =====
# =========================================================
class MusicManager:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = []      
        self.history = []    
        self.current = None  
        self.volume = 0.5    
        self.mode = "none" # none, single, all
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
        if error: print(f"播放錯誤: {error}")
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
        # 使用 call_soon_threadsafe 確保非同步安全
        self.vc.play(source, after=lambda e: bot.loop.call_soon_threadsafe(self.play_next, e))

# ===== 音樂控制按鈕 UI =====
class MusicControlView(discord.ui.View):
    def __init__(self, manager):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="上一首", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.history:
            return await interaction.response.send_message("沒有歷史紀錄", ephemeral=True)
        last = self.manager.history.pop()
        if self.manager.current: self.manager.queue.insert(0, self.manager.current)
        self.manager.queue.insert(0, last)
        self.manager.current = None
        self.manager.vc.stop()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.vc.is_playing(): self.manager.vc.pause()
        elif self.manager.vc.is_paused(): self.manager.vc.resume()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="下一首", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue:
            return await interaction.response.send_message("待播清單已空", ephemeral=True)
        self.manager.vc.stop()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="音量+", style=discord.ButtonStyle.gray)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = min(self.manager.volume + 0.1, 1.5)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

# =========================================================
# ===== 系統工具與輔助函式 =====
# =========================================================
async def get_system_info():
    cpu_usage = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    net_io = psutil.net_io_counters()
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get('https://api.ipify.org', timeout=5) as resp:
                ip = await resp.text()
        except:
            ip = "無法獲取"

    return {
        "cpu": f"{cpu_usage}%",
        "ram": f"{round(ram.used/(1024**3),2)}GB / {round(ram.total/(1024**3),2)}GB",
        "ip": ip,
        "net": f"↑{round(net_io.bytes_sent/(1024**3),2)}GB | ↓{round(net_io.bytes_recv/(1024**3),2)}GB"
    }

def get_usage_text():
    return (
        "## 機器人指令手冊\n"
        "### 管理功能\n"
        "* /移除身分組 [成員] [身分組]：將某人的身分組拔掉。\n"
        "### 音樂/掛機\n"
        "* /加入：進入語音頻道掛機。\n"
        "* /播放 [上傳檔案]：播放音樂。\n"
        "* /離開：退出頻道並清除隊列。\n"
        "### 伺服器工具\n"
        "* /設定統計頻道：建立人數統計。\n"
        "* /系統狀態：硬體負載資訊。\n"
        "* /開始標註 / /停止標註：轟炸功能。"
    )

# =========================================================
# ===== 事件與任務 (Events & Tasks) =====
# =========================================================

@bot.event
async def on_ready():
    await tree.sync()
    await bot.change_presence(activity=discord.Game(name="/使用方式"))
    print(f"機器人已啟動: {bot.user}")
    if not check_connection.is_running(): check_connection.start()
    if not update_member_stats.is_running(): update_member_stats.start()
    if not tagging_task.is_running(): tagging_task.start()

@tasks.loop(seconds=30)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        channel = bot.get_channel(cid)
        if channel:
            try: await channel.connect(self_deaf=True, self_mute=False)
            except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds:
        if guild.id in stats_channels:
            channels = stats_channels[guild.id]
            total = guild.member_count
            bots = sum(1 for m in guild.members if m.bot)
            mapping = {"total": f"全部: {total}", "members": f"成員: {total-bots}", "bots": f"機器人: {bots}"}
            for key, name in mapping.items():
                ch = bot.get_channel(channels.get(key))
                if ch and ch.name != name:
                    try: await ch.edit(name=name)
                    except: pass

@tasks.loop(seconds=2.0)
async def tagging_task():
    for gid, data in list(tag_targets.items()):
        channel = bot.get_channel(data["channel_id"])
        if not channel: continue
        try:
            await channel.send(f"<@{data['user_id']}> {data['content']}")
            if data["count"] is not None:
                data["count"] -= 1
                if data["count"] <= 0: tag_targets.pop(gid)
        except: pass

# =========================================================
# ===== Slash Commands (指令區) =====
# =========================================================

# --- [新增] 身分組管理功能 ---
@tree.command(name="移除身分組", description="移除指定成員的身分組 (限管理員或特定身分組)")
@app_commands.describe(target="目標成員", role="要移除的身分組")
async def remove_role(interaction: discord.Interaction, target: discord.Member, role: discord.Role):
    await interaction.response.defer(thinking=True)
    
    # 權限定義：管理身分組權限 或 擁有特定 ID 身分組的人
    ALLOWED_ROLE_ID = 0  # <--- 如果你有特定身分組才可使用，請在此輸入 ID
    has_perm = (
        interaction.user.guild_permissions.manage_roles or 
        any(r.id == ALLOWED_ROLE_ID for r in interaction.user.roles)
    )

    if not has_perm:
        return await interaction.followup.send("錯誤：你沒有權限執行此操作。", ephemeral=True)

    # 檢查權限層級
    if role >= interaction.guild.me.top_role:
        return await interaction.followup.send("錯誤：我的權限低於該身分組 無法移除。", ephemeral=True)
    
    if role not in target.roles:
        return await interaction.followup.send("錯誤：該成員目前沒有這個身分組。", ephemeral=True)

    try:
        await target.remove_roles(role)
        await interaction.followup.send(f"成功移除 {target.mention} 的 {role.name} 身分組。")
    except Exception as e:
        await interaction.followup.send(f"移除失敗: {e}", ephemeral=True)

# --- 音樂/系統/狀態 指令 ---

@tree.command(name="系統狀態")
async def system_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    info = await get_system_info()
    embed = discord.Embed(title="伺服器效能監控", color=0x3498db)
    embed.add_field(name="CPU", value=info["cpu"], inline=True)
    embed.add_field(name="RAM", value=info["ram"], inline=True)
    embed.add_field(name="網路狀態", value=info["net"], inline=False)
    embed.add_field(name="IP 位址", value=info["ip"], inline=False)
    await interaction.followup.send(embed=embed)

@tree.command(name="加入")
async def join(interaction: discord.Interaction):
    channel = getattr(interaction.user.voice, '頻道', None)
    if not channel: return await interaction.response.send_message("請先進入語音頻道", ephemeral=True)
    
    if interaction.guild.voice_client: await interaction.guild.voice_client.move_to(channel)
    else: await channel.connect(self_deaf=True)
    
    stay_channels[interaction.guild.id] = channel.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.response.send_message(f"我進來 {channel.name} 竊聽了")

@tree.command(name="播放")
async def play_file(interaction: discord.Interaction, 檔案: discord.Attachment):
    if not any(檔案.filename.lower().endswith(i) for i in ['.mp3', '.ogg', '.m4a', '.wav']):
        return await interaction.response.send_message("不支援的格式", ephemeral=True)
    
    await interaction.response.defer(thinking=True)
    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]

    if not interaction.guild.voice_client:
        if not interaction.user.voice: return await interaction.followup.send("請進入頻道")
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True)
        stay_channels[gid] = interaction.user.voice.channel.id
        stay_since[gid] = time.time()
    else:
        mgr.vc = interaction.guild.voice_client

    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing() and not mgr.vc.is_paused(): mgr.play_next()
    await interaction.followup.send(embed=mgr.get_status_embed(), view=MusicControlView(mgr))

@tree.command(name="離開")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        queues.pop(interaction.guild.id, None)
        await interaction.response.send_message("我走了 你別再難過")
    else:
        await interaction.response.send_message("不在頻道中", ephemeral=True)

@tree.command(name="開始標註")
async def start_tag(interaction: discord.Interaction, target: discord.Member, 內容: str, 次數: int = None):
    tag_targets[interaction.guild.id] = {
        "user_id": target.id, "content": 內容, 
        "channel_id": interaction.channel_id, "count": 次數
    }
    await interaction.response.send_message(f"開始轟炸 {target.mention}")

@tree.command(name="停止標註")
async def stop_tag(interaction: discord.Interaction):
    tag_targets.pop(interaction.guild.id, None)
    await interaction.response.send_message("已停止標註")

@tree.command(name="使用方式")
async def usage(interaction: discord.Interaction):
    await interaction.response.send_message(get_usage_text())

# --- 啟動 ---
token = os.environ.get("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    print("找不到 DISCORD_TOKEN，請檢查環境變數")
