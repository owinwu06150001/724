import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
from server import keep_alive

# 啟動 Web 服務（給 Render 用）
keep_alive()

# ===== Intents =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 掛機資料 =====
stay_channels = {}   # guild_id -> channel_id
stay_since = {}     # guild_id -> timestamp


# ===== 工具：格式化時間 =====
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


# ===== Bot Ready =====
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ 掛群機器人已上線：{bot.user}")
    check_connection.start()


# ===== /加入 =====
@tree.command(
    name="加入",
    description="加入語音頻道（可指定，或加入你目前所在的頻道）"
)
@app_commands.describe(channel="要加入的語音頻道（可不選）")
async def join(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel | None = None
):
    guild = interaction.guild
    user = interaction.user

    # 沒指定頻道 → 用使用者所在頻道
    if channel is None:
        if not user.voice:
            await interaction.response.send_message(
                "你沒選頻道也沒在語音頻道 我是要進哪",
                ephemeral=True
            )
            return
        channel = user.voice.channel

    # 已在語音就移動，否則連線
    if guild.voice_client:
        await guild.voice_client.move_to(channel)
    else:
        await channel.connect()

    stay_channels[guild.id] = channel.id
    stay_since[guild.id] = time.time()

    await interaction.response.send_message(
        f"我進來**{channel.name}**竊聽")


# ===== /離開 =====
@tree.command(
    name="離開",
    description="讓機器人離開語音頻道並停止掛機"
)
async def leave(interaction: discord.Interaction):
    guild = interaction.guild

    if guild.voice_client:
        await guild.voice_client.disconnect()
        stay_channels.pop(guild.id, None)
        stay_since.pop(guild.id, None)
        await interaction.response.send_message("我走了 你別再難過")
    else:
        await interaction.response.send_message(
            "我不在語音頻道是要離開去哪",
            ephemeral=True
        )


# ===== /狀態 =====
@tree.command(
    name="狀態",
    description="查看機器人目前掛在哪個語音頻道與掛機時間"
)
async def status(interaction: discord.Interaction):
    guild = interaction.guild

    if guild.id not in stay_channels:
        await interaction.response.send_message(
            "老子沒掛在任何語音頻道",
            ephemeral=True
        )
        return

    channel_id = stay_channels[guild.id]
    channel = bot.get_channel(channel_id)

    start_time = stay_since.get(guild.id)
    duration = int(time.time() - start_time) if start_time else 0
    duration_text = format_duration(duration)

    if not guild.voice_client:
        await interaction.response.send_message(
            f"⚠️ 記錄中掛在 **{channel.name if channel else '未知頻道'}**\n"
            f"⏱ 已掛 **{duration_text}**\n"
            "目前未連線 等待自動重連",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"🎧 目前掛在 **{channel.name}**\n"
        f"⏱ 已掛 **{duration_text}**",
        ephemeral=True
    )


# ===== 自動重連 =====
@tasks.loop(seconds=10)
async def check_connection():
    for guild_id, channel_id in list(stay_channels.items()):
        guild = bot.get_guild(guild_id)
        if not guild or guild.voice_client:
            continue

        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.connect()
                print(f"🔁 已自動重連：{guild.name}")
            except Exception as e:
                print(f"❌ 重連失敗 ({guild.name}): {e}")


# ===== 啟動 =====
bot.run(os.environ["DISCORD_TOKEN"])
