import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import json
import tempfile
import static_ffmpeg
from server import keep_alive

# ===== 1. 資料持久化邏輯 =====
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
    data = {
        "stay_channels": stay_channels,
        "stats_channels": stats_channels,
        "log_system_channels": log_system_channels
    }
    try:
        # 使用臨時檔案寫入，防止存檔時當機導致 JSON 損壞
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
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.members = True
        intents.presences = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 啟動背景任務
        update_member_stats.start()
        auto_reconnect.start()
        await self.tree.sync()

bot = MyBot()
tree = bot.tree

# 執行時變數
stay_since = {}
tag_targets = {}
queues = {}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# ===== 3. 音樂類別 =====
class MusicManager:
    def __init__(self, vc):
        self.vc = vc
        self.queue = []
        self.current = None

    def play_next(self):
        if not self.queue:
            self.current = None
            return
        
        self.current = self.queue.pop(0)
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(self.current[0], **FFMPEG_OPTIONS), 
            volume=0.5
        )
        self.vc.play(source, after=lambda e: bot.loop.call_soon_threadsafe(self.play_next))

class MusicView(discord.ui.View):
    def __init__(self, mgr):
        super().__init__(timeout=None)
        self.mgr = mgr

    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.gray)
    async def pr(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mgr.vc.is_playing():
            self.mgr.vc.pause()
            await interaction.response.send_message("已暫停", ephemeral=True)
        elif self.mgr.vc.is_paused():
            self.mgr.vc.resume()
            await interaction.response.send_message("繼續播放", ephemeral=True)
        else:
            await interaction.response.send_message("目前沒有音樂在播放", ephemeral=True)

# ===== 4. 指令區 =====

@tree.command(name="設定日誌頻道", description="設定訊息刪除/修改的記錄頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def set_log(interaction: discord.Interaction, 頻道: discord.TextChannel):
    log_system_channels[str(interaction.guild.id)] = 頻道.id
    save_config()
    await interaction.response.send_message(f"日誌頻道已設定為 {頻道.mention}")

@tree.command(name="設定統計頻道", description="建立全方位數據統計")
@app_commands.checks.has_permissions(manage_channels=True)
async def stats_setup(interaction: discord.Interaction):
    await interaction.response.defer()
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(connect=False),
        guild.me: discord.PermissionOverwrite(manage_channels=True, connect=True)
    }
    
    cat = await guild.create_category("伺服器數據", overwrites=overwrites)
    c1 = await guild.create_voice_channel("總人數: 讀取中...", category=cat)
    c2 = await guild.create_voice_channel("人類數量: 讀取中...", category=cat)
    c3 = await guild.create_voice_channel("機器人: 讀取中...", category=cat)
    c4 = await guild.create_voice_channel("在線人數: 讀取中...", category=cat)

    stats_channels[str(guild.id)] = {"total": c1.id, "humans": c2.id, "bots": c3.id, "online": c4.id}
    save_config()
    await interaction.followup.send("統計頻道已建立，數據將在 10 分鐘內同步。")

@tree.command(name="加入", description="語音掛機駐紮")
async def join(interaction: discord.Interaction, 頻道: discord.VoiceChannel = None):
    ch = 頻道 or (interaction.user.voice.channel if interaction.user.voice else None)
    if not ch: 
        return await interaction.response.send_message("請先進入語音頻道或指定頻道")
    
    await ch.connect(self_deaf=True)
    stay_channels[str(interaction.guild.id)] = ch.id
    stay_since[interaction.guild.id] = time.time()
    save_config()
    await interaction.response.send_message(f"✅ 已成功駐紮於 {ch.name}")

@tree.command(name="播放", description="上傳音檔並播放")
async def play(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer()
    
    # 檢查語音連接
    if not interaction.guild.voice_client:
        if interaction.user.voice:
            await interaction.user.voice.channel.connect(self_deaf=True)
        else:
            return await interaction.followup.send("請先進入語音頻道")
    
    # 取得或創建該伺服器的專屬隊列
    mgr = queues.get(interaction.guild.id)
    if not mgr:
        mgr = MusicManager(interaction.guild.voice_client)
        queues[interaction.guild.id] = mgr
    
    mgr.queue.append((檔案.url, 檔案.filename))
    
    if not mgr.vc.is_playing():
        mgr.play_next()
        await interaction.followup.send(f"🎵 正在播放: {檔案.filename}", view=MusicView(mgr))
    else:
        await interaction.followup.send(f"➕ 已加入隊列: {檔案.filename}")

@tree.command(name="開始標註", description="重複標註特定成員")
async def bomb(interaction: discord.Interaction, 成員: discord.Member, 內容: str, 次數: int):
    if 次數 > 50: 次數 = 50  # 防惡意過載安全閥
    tag_targets[(interaction.guild.id, 成員.id)] = True
    await interaction.response.send_message(f"🚀 開始對 {成員.display_name} 執行標註任務")
    
    for _ in range(次數):
        if not tag_targets.get((interaction.guild.id, 成員.id)): 
            break
        try:
            await interaction.channel.send(f"{成員.mention} {內容}")
        except:
            break
        await asyncio.sleep(1.5)

@tree.command(name="停止標註", description="停止當前的標註任務")
async def stop_bomb(interaction: discord.Interaction, 成員: discord.Member):
    tag_targets[(interaction.guild.id, 成員.id)] = False
    await interaction.response.send_message(f"⏹️ 已停止標註 {成員.display_name}")

# ===== 5. 自動化循環任務 =====

@tasks.loop(minutes=10)
async def update_member_stats():
    for gid_str, cids in list(stats_channels.items()):
        guild = bot.get_guild(int(gid_str))
        if not guild: continue
        
        t = guild.member_count
        b = len([m for m in guild.members if m.bot])
        h = t - b
        # 統計除了離線以外的人數
        o = len([m for m in guild.members if m.status != discord.Status.offline])
        
        mapping = {
            "total": f"📊 總人數: {t}", 
            "humans": f"👤 人類: {h}", 
            "bots": f"🤖 機器人: {b}", 
            "online": f"🟢 在線: {o}"
        }
        
        for key, name in mapping.items():
            ch_id = cids.get(key)
            if not ch_id: continue
            ch = bot.get_channel(ch_id)
            if ch and ch.name != name:
                try: 
                    await ch.edit(name=name)
                    await asyncio.sleep(1.0) # 避免速率限制
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

# ===== 6. 事件監聽 (含日誌系統) =====

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    log_id = log_system_channels.get(str(message.guild.id))
    if log_id:
        log_ch = bot.get_channel(log_id)
        if log_ch:
            embed = discord.Embed(title="🗑️ 訊息被刪除", color=0xff0000, timestamp=discord.utils.utcnow())
            embed.add_field(name="作者", value=message.author.mention)
            embed.add_field(name="頻道", value=message.channel.mention)
            embed.add_field(name="內容", value=message.content or "無文字內容", inline=False)
            await log_ch.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content: return
    log_id = log_system_channels.get(str(before.guild.id))
    if log_id:
        log_ch = bot.get_channel(log_id)
        if log_ch:
            embed = discord.Embed(title="📝 訊息已修改", color=0xffff00, timestamp=discord.utils.utcnow())
            embed.add_field(name="作者", value=before.author.mention)
            embed.add_field(name="頻道", value=before.channel.mention)
            embed.add_field(name="修改前", value=before.content, inline=False)
            embed.add_field(name="修改後", value=after.content, inline=False)
            await log_ch.send(embed=embed)

token = os.environ.get("DISCORD_TOKEN")
if token:
    bot.run(token)
