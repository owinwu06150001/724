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
import json
import tempfile

# ===== 1. 資料持久化邏輯 (保留原始欄位，僅移除過濾器) =====
DATA_FILE = "config.json"

def load_config():
    default = {
        "stay_channels": {}, 
        "stats_channels": {}, 
        "log_system_channels": {}
    }
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                for k, v in default.items():
                    if k not in data: data[k] = v
                return data
            except: return default
    return default

def save_config():
    """優化存檔邏輯，防止損壞檔案"""
    data = {
        "stay_channels": stay_channels,
        "stats_channels": stats_channels,
        "log_system_channels": log_system_channels
    }
    try:
        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(DATA_FILE)))
        with os.fdopen(fd, 'w', encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_path, DATA_FILE)
    except Exception as e:
        print(f"存檔出錯: {e}")

_config = load_config()
stay_channels = _config["stay_channels"]
stats_channels = _config["stats_channels"]
log_system_channels = _config["log_system_channels"]

# 初始化服務
static_ffmpeg.add_paths()
keep_alive()

# ===== 2. Bot 設定 =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# 保留你原本的所有執行時變數
stay_since = {}
tag_targets = {}
queues = {}

FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}

# ===== 3. 音樂與工具類 (完整保留) =====
class MusicManager:
    def __init__(self, vc):
        self.vc = vc
        self.queue = []
        self.current = None

    def play_next(self):
        if not self.queue: return
        self.current = self.queue.pop(0)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(self.current[0], **FFMPEG_OPTIONS), volume=0.5)
        self.vc.play(source, after=lambda e: bot.loop.call_soon_threadsafe(self.play_next))

class MusicView(discord.ui.View):
    def __init__(self, mgr):
        super().__init__(timeout=None)
        self.mgr = mgr
    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.gray)
    async def pr(self, i, b):
        if self.mgr.vc.is_playing(): self.mgr.vc.pause()
        else: self.mgr.vc.resume()
        await i.response.defer()

# ===== 4. 核心指令區 (完整保留，僅移除過濾器相關指令) =====

@tree.command(name="設定統計頻道", description="建立全方位數據統計")
@app_commands.checks.has_permissions(manage_channels=True)
async def stats_setup(interaction: discord.Interaction):
    await interaction.response.defer()
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False), guild.me: discord.PermissionOverwrite(manage_channels=True, connect=True)}
    
    cat = await guild.create_category("伺服器數據", overwrites=overwrites)
    c1 = await guild.create_voice_channel("總人數: 讀取中...", category=cat)
    c2 = await guild.create_voice_channel("人類數量: 讀取中...", category=cat)
    c3 = await guild.create_voice_channel("機器人: 讀取中...", category=cat)
    c4 = await guild.create_voice_channel("在線人數: 讀取中...", category=cat)

    stats_channels[str(guild.id)] = {"total": c1.id, "humans": c2.id, "bots": c3.id, "online": c4.id}
    save_config()
    await interaction.followup.send("統計頻道已建立，數據將在 10 分鐘內同步。")

@tree.command(name="加入", description="語音掛機")
async def join(interaction: discord.Interaction, 頻道: discord.VoiceChannel = None):
    ch = 頻道 or (interaction.user.voice.channel if interaction.user.voice else None)
    if not ch: return await interaction.response.send_message("請指定頻道")
    await ch.connect(self_deaf=True)
    stay_channels[str(interaction.guild.id)] = ch.id
    stay_since[interaction.guild.id] = time.time()
    save_config()
    await interaction.response.send_message(f"已駐紮於 {ch.name}")

@tree.command(name="播放", description="上傳音檔播放")
async def play(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer()
    if not interaction.guild.voice_client: 
        if interaction.user.voice:
            await interaction.user.voice.channel.connect(self_deaf=True)
        else:
            return await interaction.followup.send("請先進入語音頻道")
    
    mgr = queues.get(interaction.guild.id) or MusicManager(interaction.guild.voice_client)
    queues[interaction.guild.id] = mgr
    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing(): mgr.play_next()
    await interaction.followup.send(f"已加入隊列: {檔案.filename}", view=MusicView(mgr))

@tree.command(name="開始標註", description="轟炸")
async def bomb(interaction: discord.Interaction, 成員: discord.Member, 內容: str, 次數: int):
    tag_targets[(interaction.guild.id, 成員.id)] = True
    await interaction.response.send_message(f"開始轟炸 {成員.name}")
    for _ in range(次數):
        if not tag_targets.get((interaction.guild.id, 成員.id)): break
        await interaction.channel.send(f"{成員.mention} {內容}")
        await asyncio.sleep(1.2)

@tree.command(name="停止標註", description="停止轟炸")
async def stop_bomb(interaction: discord.Interaction, 成員: discord.Member):
    tag_targets[(interaction.guild.id, 成員.id)] = False
    await interaction.response.send_message("已停止。")

# ===== 5. 自動化循環任務 (完整保留) =====

@tasks.loop(minutes=10)
async def update_member_stats():
    for gid_str, cids in list(stats_channels.items()):
        guild = bot.get_guild(int(gid_str))
        if not guild: continue
        
        t = guild.member_count
        b = len([m for m in guild.members if m.bot])
        h = t - b
        o = len([m for m in guild.members if m.status != discord.Status.offline])
        
        mapping = {"total": f"總人數: {t}", "humans": f"人類數量: {h}", "bots": f"機器人: {b}", "online": f"在線人數: {o}"}
        for key, name in mapping.items():
            ch = bot.get_channel(cids.get(key))
            if ch and ch.name != name: 
                try: await ch.edit(name=name); await asyncio.sleep(1.2)
                except: pass

@tasks.loop(seconds=30)
async def auto_reconnect():
    for gid_str, cid in list(stay_channels.items()):
        guild = bot.get_guild(int(gid_str))
        if guild and not guild.voice_client:
            ch = bot.get_channel(cid)
            if ch: 
                try: await ch.connect(self_deaf=True)
                except: pass

# ===== 6. 事件監聽 (僅移除過濾器邏輯) =====

@bot.event
async def on_ready():
    await tree.sync()
    if not update_member_stats.is_running():
        update_member_stats.start()
    if not auto_reconnect.is_running():
        auto_reconnect.start()
    print(f"機器人已上線: {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot: return
    # 過濾器邏輯已在此處移除
    await bot.process_commands(message)

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
