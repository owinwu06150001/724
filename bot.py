import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
from server import keep_alive
import static_ffmpeg
static_ffmpeg.add_paths() # 這會自動下載 ffmpeg 並加入環境變數

# ===== 啟動 Web 服務（給 Render 用） =====
keep_alive()

# ===== Intents =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True # 用於偵測新成員加入伺服器

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 資料儲存 =====
stay_channels = {}   # guild_id -> channel_id
stay_since = {}      # guild_id -> timestamp
tag_targets = {}     # guild_id -> {"user_id": int, "content": str, "channel_id": int, "count": int|None}

# ===== 播放音檔設定 (需要 FFmpeg) =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# ===== 工具：格式化時間與用法文字 =====
def format_duration(seconds: int) -> str:
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小時")
    if minutes:
        parts.append(f"{minutes} 分")
    parts.append(f"{seconds} 秒")

    return " ".join(parts)

def get_usage_text():
    bot_mention = bot.user.mention if bot.user else "@機器人"
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 **24/7 語音掛機** 設計 具備30秒自動重連機制。\n\n"
        "### 指令列表\n"
        "* **/加入 `[頻道]`**：讓機器人進入語音頻道（可不選，預設進入你所在的頻道）。\n"
        "* **/播放 `[檔案]`**：**直接上傳** mp3, ogg, m4a 檔案進行播放。\n"
        "* **/停止播放**：停止目前播放的音檔。\n"
        "* **/離開**：讓機器人退出語音頻道並停止掛機。\n"
        "* **/開始標註 `[成員]` `[內容]` `[次數]`**：瘋狂轟炸某人（次數不填則直至機器人下線或使用者使用停止指令）。\n"
        "* **/停止標註**：結束目前的轟炸。\n"
        "* **/狀態**：查看目前掛機頻道、已掛機時間與延遲。\n"
        "* **/使用方式**：顯示此幫助選單。\n\n"
        "### 小提醒\n"
        "* 機器人每 30 秒會自動檢查連線，斷線會自動連回。"\n
        "* 此bot兼有人進來時會自動歡迎。"
    )

# ===== Bot Ready =====
@bot.event
async def on_ready():
    await tree.sync()
    
    # 設定自定義狀態
    activity = discord.Activity(
        type=discord.ActivityType.custom, 
        name="這裡不會顯示", 
        state="慢慢摸索中", 
        details="正在玩 你的感情"
    )
    
    await bot.change_presence(status=discord.Status.online, activity=activity)
    
    print(f"機器人已上線：{bot.user}")
    
    # 啟動循環任務
    if not check_connection.is_running():
        check_connection.start()
    if not tagging_task.is_running():
        tagging_task.start()

# ===== 功能：標註機器人回覆用法 =====
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if bot.user and bot.user.mentioned_in(message):
        await message.channel.send(get_usage_text())
    await bot.process_commands(message)

# ==========================================
# ===== 成員加入伺服器時：發送文字歡迎訊息 =====
# ==========================================
@bot.event
async def on_member_join(member):
    # 獲取伺服器的系統預設頻道（通常是 #一般 或 #welcome）
    channel = member.guild.system_channel
    
    if channel is not None:
        # 取得伺服器總人數
        total_members = member.guild.member_count
        
        # 發送文字歡迎
        await channel.send(
            f"歡迎 {member.mention} 加入 **{member.guild.name}**\n"
            f"你是本伺服器的第 **{total_members}** 位成員"
        )

# ==========================================

# ===== 循環任務 1：自動重連 (每 30 秒) =====
@tasks.loop(seconds=30)
async def check_connection():
    for guild_id, channel_id in list(stay_channels.items()):
        guild = bot.get_guild(guild_id)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()):
            continue
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                # 設定不靜音以利播放功能
                await channel.connect(self_deaf=True, self_mute=False)
                print(f"已自動重連：{guild.name}")
            except Exception as e:
                print(f"重連失敗 ({guild.name}): {e}")

# ===== 循環任務 2：瘋狂標註 (頻率 0.8s) =====
@tasks.loop(seconds=0.8)
async def tagging_task():
    for guild_id, data in list(tag_targets.items()):
        channel = bot.get_channel(data["channel_id"])
        if not channel: continue
        
        try:
            user_mention = f"<@{data['user_id']}>"
            await channel.send(f"{user_mention} {data['content']}")
            
            if data["count"] is not None:
                data["count"] -= 1
                if data["count"] <= 0:
                    tag_targets.pop(guild_id)
        except discord.errors.HTTPException as e:
            if e.status == 429: # Rate Limit
                await asyncio.sleep(3)
        except:
            pass

# ===== Slash Commands =====

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

    if guild.voice_client:
        await guild.voice_client.move_to(channel)
    else:
        await channel.connect(self_deaf=True, self_mute=False)

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
            # 確保機器人未被靜音
            await guild.me.edit(mute=False)

        if vc.is_playing():
            vc.stop()

        source = discord.FFmpegPCMAudio(檔案.url, **FFMPEG_OPTIONS)
        vc.play(source, after=lambda e: print(f"播放結束: {e}") if e else None)
        
        await interaction.followup.send(f"正在播放：**{檔案.filename}**")
        
    except Exception as e:
        await interaction.followup.send(f"播放失敗：{e}")

@tree.command(name="停止播放", description="停止目前播放的音檔")
async def stop_audio(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("已停止播放。")
    else:
        await interaction.response.send_message("目前沒有正在播放的音檔。", ephemeral=True)

@tree.command(name="開始標註", description="瘋狂標註某人")
@app_commands.describe(target="對象", 內容="內容", 次數="次數 (不填則持續標註)")
async def start_tag(interaction: discord.Interaction, target: discord.Member, 內容: str, 次數: int | None = None):
    tag_targets[interaction.guild.id] = {
        "user_id": target.id,
        "content": 內容,  
        "channel_id": interaction.channel_id,
        "count": 次數
    }
    await interaction.response.send_message(f"開始轟炸 {target.mention}！內容：{內容}")

@tree.command(name="停止標註", description="停止目前的標註任務")
async def stop_tag(interaction: discord.Interaction):
    if interaction.guild_id in tag_targets:
        tag_targets.pop(interaction.guild_id)
        await interaction.response.send_message("已停止轟炸 饒他一命。")
    else:
        await interaction.response.send_message("目前沒有正在進行的轟炸任務。", ephemeral=True)

@tree.command(name="狀態", description="查看掛機與延遲狀態")
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

    await interaction.followup.send(
        f"目前在 **{channel.name if channel else '未知'}** 竊聽中\n"
        f"已竊聽 **{duration_text}**\n"
        f"延遲：{latency_ms} ms",
        ephemeral=True
    )

@tree.command(name="延遲", description="檢查機器人延遲")
async def latency(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await interaction.followup.send(f"本公子的延遲為: {round(bot.latency * 1000)} ms", ephemeral=True)

# ===== 啟動 =====
token = os.environ.get("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    print("錯誤：找不到 DISCORD_TOKEN 環境變數")


