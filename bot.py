import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
from server import keep_alive
import static_ffmpeg
import psutil
import requests

static_ffmpeg.add_paths()

# ===== 啟動 Web 服務 =====
keep_alive()

# ===== Intents =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 資料儲存 =====
stay_channels = {}
stay_since = {}
queues = {} # guild_id -> MusicManager
stats_channels = {}

# 紀錄啟動時的流量初始值
boot_net_io = psutil.net_io_counters()

# ===== 播放音檔設定 =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# =========================================================
# ===== 音樂管理類別 (處理隊列與模式) =====
# =========================================================
class MusicManager:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = []      # 存放 (url, filename)
        self.history = []    # 存放播放過的
        self.current = None  # 目前播放的 (url, filename)
        self.volume = 0.5    
        self.mode = "none"   # none, single, all
        self.vc = None

    def play_next(self, error=None):
        if not self.vc or not self.vc.is_connected(): return

        # 循環模式判斷
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
        self.vc.play(source, after=lambda e: self.play_next(e))

# =========================================================
# ===== UI：進階按鈕控制面板 (無圖片版) =====
# =========================================================
class MusicControlView(discord.ui.View):
    def __init__(self, manager):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="⏮️ 上一首", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.history:
            return await interaction.response.send_message("沒有上一首紀錄", ephemeral=True)
        last = self.manager.history.pop()
        if self.manager.current: self.manager.queue.insert(0, self.manager.current)
        self.manager.queue.insert(0, last)
        self.manager.current = None
        self.manager.vc.stop()
        await interaction.response.send_message(f"已回退至: {last[1]}", ephemeral=True)

    @discord.ui.button(label="⏯️ 暫停/繼續", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.vc.is_playing():
            self.manager.vc.pause()
            await interaction.response.send_message("已暫停播放", ephemeral=True)
        elif self.manager.vc.is_paused():
            self.manager.vc.resume()
            await interaction.response.send_message("繼續播放", ephemeral=True)
        else:
            await interaction.response.send_message("目前沒在播放", ephemeral=True)

    @discord.ui.button(label="⏭️ 下一首", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue:
            return await interaction.response.send_message("清單已空", ephemeral=True)
        self.manager.vc.stop()
        await interaction.response.send_message("跳過當前歌曲", ephemeral=True)

    @discord.ui.button(label="🔄 循環: 關閉", style=discord.ButtonStyle.gray)
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.mode == "none":
            self.manager.mode = "single"
            button.label = "🔄 循環: 單曲"
            button.style = discord.ButtonStyle.success
        elif self.manager.mode == "single":
            self.manager.mode = "all"
            button.label = "🔄 循環: 全清單"
            button.style = discord.ButtonStyle.primary
        else:
            self.manager.mode = "none"
            button.label = "🔄 循環: 關閉"
            button.style = discord.ButtonStyle.gray
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="📜 待播清單", style=discord.ButtonStyle.success)
    async def queue_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue:
            return await interaction.response.send_message("清單是空的", ephemeral=True)
        msg = "\n".join([f"{i+1}. {s[1]}" for i, s in enumerate(self.manager.queue[:10])])
        await interaction.response.send_message(f"**待播清單 (前10首):**\n{msg}", ephemeral=True)

    @discord.ui.button(label="🔊 音量+", style=discord.ButtonStyle.gray)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = min(self.manager.volume + 0.1, 2.0)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.send_message(f"音量: {int(self.manager.volume*100)}%", ephemeral=True)

    @discord.ui.button(label="🔉 音量-", style=discord.ButtonStyle.gray)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = max(self.manager.volume - 0.1, 0.0)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.send_message(f"音量: {int(self.manager.volume*100)}%", ephemeral=True)

# ===== 工具函式 =====
def format_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{d}天 {h}時 {m}分 {s}秒"

def get_size(bytes):
    for unit in ['', 'K', 'M', 'G']:
        if bytes < 1024: return f"{bytes:.2f} {unit}B"
        bytes /= 1024

# ===== 指令區 =====

@tree.command(name="播放", description="上傳音檔播放並開啟控制面板")
@app_commands.describe(檔案="請選擇要上傳的音檔")
async def play_file(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer(thinking=True)
    if not any(檔案.filename.lower().endswith(i) for i in ['.mp3', '.ogg', '.m4a', '.wav']):
        return await interaction.followup.send("不支援此格式", ephemeral=True)
    if not interaction.user.voice:
        return await interaction.followup.send("請先進入語音頻道", ephemeral=True)

    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]

    if not interaction.guild.voice_client:
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True)
        stay_channels[gid] = interaction.user.voice.channel.id
        stay_since[gid] = time.time()
    else:
        mgr.vc = interaction.guild.voice_client

    mgr.queue.append((檔案.url, 檔案.filename))

    if not mgr.vc.is_playing() and not mgr.vc.is_paused():
        mgr.play_next()
        title_msg = f"正在播放: **{檔案.filename}**"
    else:
        title_msg = f"已加入清單: **{檔案.filename}**"

    embed = discord.Embed(title="音樂控制中心", description=title_msg, color=0xaa96da)
    embed.set_footer(text=f"音量: {int(mgr.volume*100)}% | 模式: {mgr.mode}")
    
    await interaction.followup.send(embed=embed, view=MusicControlView(mgr))

@tree.command(name="狀態", description="查看機器人資源與延遲")
async def status(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    cpu = psutil.cpu_percent()
    mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    await interaction.response.send_message(
        f"延遲: {latency}ms | CPU: {cpu}% | 記憶體: {mem:.1f}MB", ephemeral=True
    )

@tree.command(name="離開", description="停止播放並退出語音")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        queues.pop(interaction.guild_id, None)
        stay_channels.pop(interaction.guild_id, None)
        await interaction.response.send_message("已斷開連接並清理清單。")
    else:
        await interaction.response.send_message("我不在語音頻道中", ephemeral=True)

@bot.event
async def on_ready():
    await tree.sync()
    print(f"機器人已上線: {bot.user}")

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
