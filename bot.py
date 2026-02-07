import discord
from discord.ext import commands, tasks
import os
from server import keep_alive

# 啟動 Web 服務供 Render 偵測
keep_alive()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 儲存掛群頻道的資訊
stay_channels = {}

@bot.event
async def on_ready():
    print(f"掛群機器人已上線: {bot.user}")
    check_connection.start()

# 指令: !join (加入你目前的語音頻道)
@bot.command()
async def join(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        await channel.connect()
        stay_channels[ctx.guild.id] = channel.id
        await ctx.send(f"已進入 {channel.name} 開始 24/7 掛機")
    else:
        await ctx.send("你沒進頻道我要去個屌毛")

# 指令: !leave
@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        guild_id = ctx.guild.id
        await ctx.voice_client.disconnect()
        if guild_id in stay_channels:
            del stay_channels[guild_id]
        await ctx.send("已停止並離開")
    else:
        await ctx.send("我不在裡面是要離開去哪裡")

# 自動保活任務：每 1 秒檢查一次是否斷線
@tasks.loop(seconds=1)
async def check_connection():
    for guild_id, channel_id in stay_channels.items():
        guild = bot.get_guild(guild_id)
        if guild:
            # 如果機器人不在語音頻道中，嘗試重連
            if not guild.voice_client:
                channel = bot.get_channel(channel_id)
                if channel:
                    try:
                        await channel.connect()
                        print(f"已自動恢復 {guild.name} 的掛機狀態")
                    except Exception as e:
                        print(f"重連失敗: {e}")

bot.run(os.environ["DISCORD_TOKEN"])