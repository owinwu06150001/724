import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
from server import keep_alive

# ===== 啟動 Web 服務（給 Render 用） =====
keep_alive()

# ===== Intents 設定 =====
intents = discord.Intents.default()
intents.message_content = True  # 讀取訊息內容 (標註回覆用)
intents.voice_states = True      # 語音狀態偵測
intents.members = True           # 取得成員資訊 (標註功能用)

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 資料儲存 =====
stay_channels = {}   # guild_id -> channel_id (掛機頻道)
stay_since = {}      # guild_id -> timestamp (開始時間)
tag_targets = {}     # guild_id -> {"user_id": int, "content": str, "channel_id": int} (標註資料)

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

def get_usage_text():
    bot_mention = bot.user.mention if bot.user else "@機器人"
    return (
        f"## {bot_mention} 指令手冊\n"
        "### 🎙️ 語音掛機\n"
        "* **/加入 `[頻道]`**：進入語音頻道（預設為你所在的頻道）。\n"
        "* **/離開**：退出語音並停止掛機。\n"
        "* **/狀態**：查看掛機時長與延遲。\n\n"
        "### 📣 標註功能\n"
        "* **/開始標註 `[成員]` `[內容]`**：瘋狂 Tag 某人。\n"
        "* **/停止標註**：結束目前的 Tag 轟炸。\n\n"
        "### 其他\n"
        "* **/延遲**：檢查機器人延遲。\n"
        "* 直接 **標註機器人** 也能叫出此選單。"
    )

# ===== 事件處理 =====
@bot.event
async def on_ready():
    await tree.sync()
    print(f"機器人已上線：{bot.user}")
    
    # 啟動循環任務
    if not check_connection.is_running():
        check_connection.start()
    if not tagging_task.is_running():
        tagging_task.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if bot.user and bot.user.mentioned_in(message):
        await message.channel.send(get_usage_text())
    await bot.process_commands(message)

# ===== 循環任務 1：自動重連 (每 30 秒) =====
@tasks.loop(seconds=30)
async def check_connection():
    for guild_id, channel_id in list(stay_channels.items()):
        guild = bot.get_guild(guild_id)
        if not guild: continue
        
        # 如果沒在語音頻道，嘗試連回
        if not guild.voice_client or not guild.voice_client.is_connected():
            channel = bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.connect(self_deafen=True)
                    print(f"已自動重連至 {guild.name} 的 {channel.name}")
                except Exception as e:
                    print(f"重連失敗: {e}")

# ===== 循環任務 2：瘋狂標註 (每 2 秒) =====
@tasks.loop(seconds=2)
async def tagging_task():
    for guild_id, data in list(tag_targets.items()):
        channel = bot.get_channel(data["channel_id"])
        if channel:
            try:
                user_mention = f"<@{data['user_id']}>"
                await channel.send(f"{user_mention} {data['content']}")
            except Exception:
                pass # 避免權限不足導致任務中斷

# ===== Slash Commands =====

@tree.command(name="使用方式", description="顯示指令列表")
async def usage(interaction: discord.Interaction):
    await interaction.response.send_message(get_usage_text())

@tree.command(name="加入", description="加入語音頻道掛機")
@app_commands.describe(channel="要加入的頻道")
async def join(interaction: discord.Interaction, channel: discord.VoiceChannel | None = None):
    await interaction.response.defer()
    target_channel = channel or getattr(interaction.user.voice, 'channel', None)
    
    if not target_channel:
        return await interaction.followup.send("你要我進去哪？請先加入語音頻道或指定頻道。")

    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(target_channel)
    else:
        await target_channel.connect(self_deafen=True)

    stay_channels[interaction.guild_id] = target_channel.id
    stay_since[interaction.guild_id] = time.time()
    await interaction.followup.send(f"已進入 **{target_channel.name}** 開始 24/7 監聽。")

@tree.command(name="離開", description="離開語音頻道")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild_id, None)
        stay_since.pop(interaction.guild_id, None)
        await interaction.response.send_message("下班了，大家掰掰。")
    else:
        await interaction.response.send_message("我目前不在任何語音頻道。", ephemeral=True)

@tree.command(name="狀態", description="查看掛機狀態")
async def status(interaction: discord.Interaction):
    if interaction.guild_id not in stay_channels:
        return await interaction.response.send_message("目前沒有掛機任務。", ephemeral=True)

    start_time = stay_since.get(interaction.guild_id, time.time())
    duration = format_duration(int(time.time() - start_time))
    latency = round(bot.latency * 1000)
    
    await interaction.response.send_message(
        f"✅ **掛機中**\n時長：`{duration}`\n延遲：`{latency}ms`", 
        ephemeral=True
    )

@tree.command(name="開始標註", description="瘋狂 Tag 某人")
@app_commands.describe(target="要 Tag 的對象", content="內容")
async def start_tag(interaction: discord.Interaction, target: discord.Member, content: str):
    tag_targets[interaction.guild_id] = {
        "user_id": target.id,
        "content": content,
        "channel_id": interaction.channel_id
    }
    await interaction.response.send_message(f"🚨 轟炸開始！目標：{target.mention}，內容：{content}")

@tree.command(name="停止標註", description="停止現在的轟炸任務")
async def stop_tag(interaction: discord.Interaction):
    if interaction.guild_id in tag_targets:
        tag_targets.pop(interaction.guild_id)
        await interaction.response.send_message("轟炸已停止，世界恢復和平。")
    else:
        await interaction.response.send_message("目前沒有人在被標註。", ephemeral=True)

@tree.command(name="延遲", description="檢查延遲")
async def latency(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! `{round(bot.latency * 1000)}ms`", ephemeral=True)

# ===== 啟動機器人 =====
token = os.environ.get("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    print("錯誤：找不到 DISCORD_TOKEN 環境變數")
