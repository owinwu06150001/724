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
tag_targets = {}  # 儲存要炸的人
queues = {}       # guild_id -> MusicManager
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
        self.queue = []      
        self.history = []    
        self.current = None  
        self.volume = 0.5    
        self.mode = "none"   
        self.vc = None

    def play_next(self, error=None):
        if not self.vc or not self.vc.is_connected(): return
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
            return await interaction.response.send_message("沒有歷史紀錄", ephemeral=True)
        last = self.manager.history.pop()
        if self.manager.current: self.manager.queue.insert(0, self.manager.current)
        self.manager.queue.insert(0, last)
        self.manager.current = None
        self.manager.vc.stop()
        await interaction.response.send_message(f"回退至: {last[1]}", ephemeral=True)

    @discord.ui.button(label="⏯️ 暫停/繼續", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.vc.is_playing():
            self.manager.vc.pause()
            await interaction.response.send_message("已暫停", ephemeral=True)
        elif self.manager.vc.is_paused():
            self.manager.vc.resume()
            await interaction.response.send_message("繼續播放", ephemeral=True)

    @discord.ui.button(label="⏭️ 下一首", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue:
            return await interaction.response.send_message("清單已空", ephemeral=True)
        self.manager.vc.stop()
        await interaction.response.send_message("跳過歌曲", ephemeral=True)

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
    async def q_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue:
            return await interaction.response.send_message("清單為空", ephemeral=True)
        msg = "\n".join([f"{i+1}. {s[1]}" for i, s in enumerate(self.manager.queue[:10])])
        await interaction.response.send_message(f"**待播清單:**\n{msg}", ephemeral=True)

    @discord.ui.button(label="🔊 音量+", style=discord.ButtonStyle.gray)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = min(self.manager.volume + 0.1, 2.0)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.send_message(f"音量: {int(self.manager
