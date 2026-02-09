import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
from server import keep_alive

# ===== 啟動 Web 服務（給 Render 用） =====
keep_alive()

# ===== Intents =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 掛機資料 =====
stay_channels = {}   # guild_id -> channel_id
stay_since = {}      # guild_id -> timestamp

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
    # 確保機器人上線後能抓到 mention，否則預設文字
    bot_mention = bot.user.mention if bot.user else "@機器人"
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 **24/7 語音掛機** 設計 具備自動重連機制。\n\n"
        "### 指令列表\n"
        "* **/加入 `[頻道]`**：讓機器人進入語音頻道（可不選，預設進入你所在的頻道）。\n"
        "* **/離開**：讓機器人退出語音頻道並停止掛機。\n"
        "* **/狀態**：查看目前掛機頻道、已掛機時間與延遲。\n"
        "* **/延遲**：檢查機器人當前延遲 (ms)。\n"
        "* **/使用方式**：顯示此幫助選單。\n\n"
        "### 小提醒\n"
        "* 機器人每 30 秒會自動檢查連線，斷線會自動連回。"
    )

# ===== Bot Ready =====
@bot.event
async def on_ready():
    await tree.sync()
    print(f"掛群機器人已上線：{bot.user}")
    check_connection.start()

# ===== 功能：標註機器人回覆用法 =====
@bot.event
async def on_message(message):
    # 排除機器人自己的訊息
    if message.author.bot:
        return
    
    # 判斷訊息是否標註了機器人
    if bot.user and bot.user.mentioned_in(message):
        await message.channel.send(get_usage_text())
    
    # 處理其他 prefix 指令 (雖然主要用 slash)
    await bot.process_commands(message)

# ===== Slash Commands =====

@tree.command(name="使用方式", description="顯示機器人的指令列表與詳細用法")
async def usage(interaction: discord.Interaction):
    await interaction.response.send_message(get_usage_text())

@tree.command(name="加入", description="加入語音頻道")
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
        # 加入 self_deafen=True
        await channel.connect(self_deaf=True)

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

@tree.command(name="狀態", description="查看掛機狀態")
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

@tree.command(name="延遲", description="檢查延遲")
async def latency(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await interaction.followup.send(f"本公子的延遲為: {round(bot.latency * 1000)} ms", ephemeral=True)

# ===== 自動重連任務 =====
@tasks.loop(seconds=30)
async def check_connection():
    for guild_id, channel_id in list(stay_channels.items()):
        guild = bot.get_guild(guild_id)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()):
            continue
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                # 重連時也加入 self_deafen=True
                await channel.connect(self_deafen=True)
                print(f"已自動重連：{guild.name}")
            except Exception as e:
                print(f"重連失敗 ({guild.name}): {e}")

# ===== 啟動 =====
token = os.environ.get("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    print("錯誤：找不到 DISCORD_TOKEN 環境變數")

