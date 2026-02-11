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

# ===== 啟動 Web 服務（給 Render 用） =====
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
tag_targets = {}
stats_channels = {}

# --- 紀錄啟動時的流量初始值 ---
boot_net_io = psutil.net_io_counters()

# ===== 播放音檔設定 (需要 FFmpeg) =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# =========================================================
# ===== 新增功能：UI 按鈕控制面板 (Persistent View) =====
# =========================================================
class MusicControlView(discord.ui.View):
    def __init__(self, vc):
        super().__init__(timeout=None) # 按鈕長期有效
        self.vc = vc

    @discord.ui.button(label="暫停 / 繼續", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.vc or not self.vc.is_connected():
            return await interaction.response.send_message("機器人已不在語音頻道中", ephemeral=True)
            
        if self.vc.is_playing():
            self.vc.pause()
            await interaction.response.send_message("已暫停播放 ⏸️", ephemeral=True)
        elif self.vc.is_paused():
            self.vc.resume()
            await interaction.response.send_message("繼續播放 ▶️", ephemeral=True)
        else:
            await interaction.response.send_message("目前沒有音樂在播放", ephemeral=True)

    @discord.ui.button(label="停止播放", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc and self.vc.is_connected():
            self.vc.stop()
            await interaction.response.send_message("音樂已停止 ⏹️", ephemeral=True)
        else:
            await interaction.response.send_message("機器人目前沒有在播放", ephemeral=True)
# =========================================================

# ===== 工具：格式化時間與用法文字 =====
def format_duration(seconds: int) -> str:
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days: parts.append(f"{days} 天")
    if hours: parts.append(f"{hours} 小時")
    if minutes: parts.append(f"{minutes} 分")
    parts.append(f"{seconds} 秒")
    return " ".join(parts)

def get_size(bytes):
    for unit in ['', 'K', 'M', 'G', 'T', 'P']:
        if bytes < 1024: return f"{bytes:.2f} {unit}B"
        bytes /= 1024

def get_public_ip():
    try: return requests.get('https://api.ipify.org', timeout=5).text
    except: return "無法取得"

def get_usage_text():
    bot_mention = bot.user.mention if bot.user else "@機器人"
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 **24/7 語音掛機** 設計。\n\n"
        "### 指令列表\n"
        "* **/加入 `[頻道]`**：讓機器人進入語音頻道。\n"
        "* **/設定統計頻道**：建立自動更新人數的統計頻道。\n"
        "* **/播放 `[檔案]`**：**直接上傳** mp3, ogg, m4a 檔案進行播放。\n"
        "* **/停止播放**：停止目前播放的音檔。\n"
        "* **/離開**：讓機器人退出語音頻道並停止掛機。\n"
        "* **/狀態**：查看目前掛機頻道、已掛機時間與延遲。"
    )

# --- 更新統計頻道邏輯 ---
async def update_stats_logic(guild):
    if guild.id not in stats_channels: return
    channels = stats_channels[guild.id]
    total = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    mapping = {
        "total": f"全部: {total}",
        "members": f"Members: {total - bots}",
        "bots": f"Bots: {bots}"
    }
    for key, new_name in mapping.items():
        channel = bot.get_channel(channels.get(key))
        if channel and channel.name != new_name:
            try: await channel.edit(name=new_name)
            except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds: await update_stats_logic(guild)

# ===== Bot Ready =====
@bot.event
async def on_ready():
    await tree.sync()
    activity = discord.Activity(
        type=discord.ActivityType.custom, 
        name="這裡不會顯示", 
        state="慢慢摸索中", 
        details="正在玩 你的感情"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)
    print(f"機器人已上線：{bot.user}")
    psutil.cpu_percent(interval=None) # 初始化
    if not check_connection.is_running(): check_connection.start()
    if not update_member_stats.is_running(): update_member_stats.start()

@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user and bot.user.mentioned_in(message):
        await message.channel.send(get_usage_text())
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if channel is not None:
        total_members = member.guild.member_count
        embed = discord.Embed(
            title=f"歡迎加入 {member.guild.name}",
            description=f"{member.mention}",
            color=discord.Color.from_rgb(255, 105, 180)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"你是本伺服器的第 {total_members} 位成員")
        await channel.send(embed=embed)
    await update_stats_logic(member.guild)

@bot.event
async def on_member_remove(member):
    await update_stats_logic(member.guild)

# ===== 循環任務 1：自動重連 (每 30 秒) =====
@tasks.loop(seconds=30)
async def check_connection():
    for guild_id, channel_id in list(stay_channels.items()):
        guild = bot.get_guild(guild_id)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.connect(self_deaf=True, self_mute=False)
                print(f"已自動重連：{guild.name}")
            except Exception as e:
                print(f"重連失敗 ({guild.name}): {e}")

# ===== Slash Commands =====

@tree.command(name="設定統計頻道", description="建立顯示伺服器人數的統計頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def setup_stats(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(connect=False),
        guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True)
    }
    try:
        category = await guild.create_category(" 伺服器數據", position=0)
        total = guild.member_count
        bots = sum(1 for m in guild.members if m.bot)
        c_total = await guild.create_voice_channel(f"全部: {total}", category=category, overwrites=overwrites)
        c_members = await guild.create_voice_channel(f"人類: {total - bots}", category=category, overwrites=overwrites)
        c_bots = await guild.create_voice_channel(f"Bots: {bots}", category=category, overwrites=overwrites)
        stats_channels[guild.id] = {"total": c_total.id, "members": c_members.id, "bots": c_bots.id}
        await interaction.followup.send("統計頻道與 Embed 歡迎功能已準備就緒！")
    except Exception as e:
        await interaction.followup.send(f"建立失敗：{e}")

@tree.command(name="使用方式", description="顯示機器人的指令列表與詳細用法")
async def usage(interaction: discord.Interaction):
    await interaction.response.send_message(get_usage_text())

@tree.command(name="加入", description="讓機器人進入語音頻道掛機")
@app_commands.describe(channel="要加入的語音頻道（可不選）")
async def join(interaction: discord.Interaction, channel: discord.VoiceChannel | None = None):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    user = interaction.user
    channel = channel or getattr(user.voice, 'channel', None)
    if not channel:
        await interaction.followup.send("你沒選頻道也沒在語音頻道 我要進哪", ephemeral=True)
        return
    if guild.voice_client: await guild.voice_client.move_to(channel)
    else: await channel.connect(self_deaf=True, self_mute=False)
    stay_channels[guild.id] = channel.id
    stay_since[guild.id] = time.time()
    await interaction.followup.send(f"我進來 **{channel.name}** 竊聽了")

@tree.command(name="離開", description="讓機器人離開語音頻道")
async def leave(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    if guild.voice_client:
        await guild.voice_client.disconnect()
        stay_channels.pop(guild.id, None)
        stay_since.pop(guild.id, None)
        await interaction.followup.send("我走了 你別再難過")
    else:
        await interaction.followup.send("我不在語音頻道 要離開去哪？", ephemeral=True)

# =========================================================
# ===== 新增功能：精美的 Embed 播放訊息與組合按鈕 =====
# =========================================================
@tree.command(name="播放", description="直接上傳音檔 (mp3, ogg, m4a) 進行播放")
@app_commands.describe(檔案="請選擇要上傳的音檔")
async def play_file(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer(thinking=True)
    
    ext = 檔案.filename.lower()
    if not any(ext.endswith(i) for i in ['.mp3', '.ogg', '.m4a', '.wav']):
        return await interaction.followup.send("格式不支援！請上傳音檔。", ephemeral=True)

    guild = interaction.guild
    if not interaction.user.voice:
        return await interaction.followup.send("你必須先進入一個語音頻道！", ephemeral=True)
    
    try:
        if not guild.voice_client:
            vc = await interaction.user.voice.channel.connect(self_deaf=True, self_mute=False)
            stay_channels[guild.id] = interaction.user.voice.channel.id
            stay_since[guild.id] = time.time()
        else:
            vc = guild.voice_client
            await guild.me.edit(mute=False)

        if vc.is_playing(): vc.stop()

        source = discord.FFmpegPCMAudio(檔案.url, **FFMPEG_OPTIONS)
        vc.play(source, after=lambda e: print(f"播放結束: {e}") if e else None)
        
        # --- 精美 Embed 播放訊息 ---
        embed = discord.Embed(
            title="🎵 音樂播放中",
            description=f"正在為您播放：**{檔案.filename}**",
            color=discord.Color.from_rgb(170, 150, 218) # 夢幻紫
        )
        # 這裡放入你圖片中的 Lofi 圖片連結
        embed.set_image(url="https://i.imgur.com/G5vUa50.gif") 
        embed.add_field(name="請求者", value=interaction.user.mention, inline=True)
        embed.set_footer(text="提示：點擊下方按鈕可快速控制播放狀態")
        
        # 組合按鈕控制面板
        view = MusicControlView(vc)
        
        await interaction.followup.send(embed=embed, view=view)
        
    except Exception as e:
        await interaction.followup.send(f"播放失敗：{e}")
# =========================================================

@tree.command(name="停止播放", description="停止目前播放的音檔")
async def stop_audio(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("已停止播放。")
    else:
        await interaction.response.send_message("目前沒有正在播放的音檔。", ephemeral=True)

@tree.command(name="狀態", description="檢查掛機與延遲狀態")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    if guild.id not in stay_channels:
        await interaction.followup.send("老子沒掛在任何語音頻道", ephemeral=True)
        return
    channel_id = stay_channels[guild.id]
    channel = bot.get_channel(channel_id)
    start_time = stay_since.get(guild.id)
    duration_text = format_duration(int(time.time() - start_time)) if start_time else "未知"
    latency_ms = round(bot.latency * 1000)
    current_io = psutil.net_io_counters()
    sent = current_io.bytes_sent - boot_net_io.bytes_sent
    recv = current_io.bytes_recv - boot_net_io.bytes_recv
    process = psutil.Process(os.getpid())
    mem_used = process.memory_info().rss / (1024 * 1024)
    cpu_usage = psutil.cpu_percent(interval=None)
    ip_addr = get_public_ip()
    await interaction.followup.send(
        f"目前在 **{channel.name if channel else '未知'}** 竊聽中\n"
        f"已竊聽 **{duration_text}**\n"
        f"延遲：{latency_ms} ms\n"
        f"--- 系統資源 ---\n"
        f"IP 位址：{ip_addr}\n"
        f"CPU 使用率：{cpu_usage}%\n"
        f"記憶體佔用：{mem_used:.2f} MB\n"
        f"本次累計上傳：{get_size(sent)}\n"
        f"本次累計下載：{get_size(recv)}",
        ephemeral=True
    )

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
else: print("錯誤：找不到 DISCORD_TOKEN 環境變數")
