import discord
from discord import app_commands
from discord.ext import commands
import os
import asyncio
from flask import Flask
from threading import Thread

# --- 基礎設定 ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'{bot.user} 已上線並同步指令')

# --- 審核日誌功能 (範例：監聽踢人行為) ---
@bot.event
async def on_member_remove(member):
    # 當有人離開/被踢時，檢查審核日誌
    async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
        if entry.target == member:
            print(f"成員 {member.name} 被 {entry.user.name} 踢出，原因: {entry.reason}")
            # 這裡可以發送到你的管理日誌頻道
            break

# --- 管理指令集 ---

@bot.tree.command(name="清除", description="清除指定數量的訊息")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    deleted = await interaction.channel.purge(limit=min(amount, 100))
    await interaction.response.send_message(f"已刪除 {len(deleted)} 條訊息", ephemeral=True)

@bot.tree.command(name="禁言", description="禁言成員")
@app_commands.checks.has_permissions(manage_roles=True)
async def mute(interaction: discord.Interaction, member: discord.Member):
    role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not role:
        return await interaction.response.send_message("請先建立名為 Muted 的身分組")
    await member.add_roles(role)
    await interaction.response.send_message(f"已禁言 {member.mention}")

@bot.tree.command(name="鎖定頻道", description="禁止所有人發言")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.response.send_message("頻道已鎖定")

@bot.tree.command(name="解鎖頻道", description="恢復發言權限")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.response.send_message("頻道已解鎖")

# --- 語音掛機指令 (保持原先邏輯) ---
@bot.tree.command(name="加入", description="加入語音")
async def join(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    target = channel or interaction.user.voice.channel
    await target.connect()
    await interaction.response.send_message(f"已加入 {target.name}")

@bot.tree.command(name="離開", description="離開語音")
async def leave(interaction: discord.Interaction):
    await interaction.guild.voice_client.disconnect()
    await interaction.response.send_message("已離開")

# --- 啟動 ---
if __name__ == "__main__":
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()
    bot.run(os.environ['DISCORD_TOKEN'])
