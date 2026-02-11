import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
from server import keep_alive
import static_ffmpeg
import psutil

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
tag_targets = {}
stats_channels = {}
queues = {} # 儲存音樂隊列與狀態

# ===== 播放音檔設定 =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# =========================================================
# ===== 新增：音樂管理系統 (支援隊列) =====
# =========================================================
class MusicManager:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = []      
        self.history = []    
        self.current = None  
        self.volume = 0.5    
        self.mode = "none" # none, single, all
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
# ===== 新增：無圖片控制面板 (按鈕 UI) =====
# =========================================================
class MusicControlView(discord.ui.View):
    def __init__(self, manager):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="上一首", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.history:
            return await interaction.response.send_message("沒有歷史紀錄", ephemeral=True)
        last = self.manager.history.pop()
        if self.manager.current: self.manager.queue.insert(0, self.manager.current)
        self.manager.queue.insert(0, last)
        self.manager.current = None
        self.manager.vc.stop()
        await interaction.response.send_message(f"已回退: {last[1]}", ephemeral=True)

    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.vc.is_playing():
            self.manager.vc.pause()
            await interaction.response.send_message("已暫停播放", ephemeral=True)
        elif self.manager.vc.is_paused():
            self.manager.vc.resume()
            await interaction.response.send_message("繼續播放", ephemeral=True)

    @discord.ui.button(label="下一首", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue:
            return await interaction.response.send_message("待播清單已空", ephemeral=True)
        self.manager.vc.stop()
        await interaction.response.send_message("跳過當前歌曲", ephemeral=True)

    @discord.ui.button(label="循環切換", style=discord.ButtonStyle.gray)
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        modes = {"none": "single", "single": "all", "all": "none"}
        labels = {"none": "循環: 關閉", "single": "循環: 單曲", "all": "循環: 全清單"}
        self.manager.mode = modes[self.manager.mode]
        button.label = labels[self.manager.mode]
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="待播清單", style=discord.ButtonStyle.success)
    async def show_q(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue: return await interaction.response.send_message("清單為空", ephemeral=True)
        msg = "\n".join([f"{i+1}. {s[1]}" for i, s in enumerate(self.manager.queue[:10])])
        await interaction.response.send_message(f"**待播清單 (前10首):**\n{msg}", ephemeral=True)

    @discord.ui.button(label="音量+", style=discord.ButtonStyle.gray)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = min(self.manager.volume + 0.1, 2.0)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.send_message(f"音量已調至：{int(self.manager.volume*100)}%", ephemeral=True)

    @discord.ui.button(label="音量-", style=discord.ButtonStyle.gray)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = max(self.manager.volume - 0.1, 0.0)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.send_message(f"音量已調至：{int(self.manager.volume*100)}%", ephemeral=True)

# ===== 原始工具函式 (保留文字) =====
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
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 **24/7 語音掛機** 設計 具備30秒自動重連機制。\n\n"
        "### 指令列表\n"
        "* **/加入 `[頻道]`**：讓機器人進入語音頻道（可不選，預設進入你所在的頻道）。\n"
        "* **/設定統計頻道**：建立自動更新人數的統計頻道。\n"
        "* **/播放 `[檔案]`**：**直接上傳** mp3, ogg, m4a 檔案進行播放。\n"
        "* **/停止播放**：停止目前播放的音檔。\n"
        "* **/離開**：讓機器人退出語音頻道並停止掛機。\n"
        "* **/開始標註 `[成員]` `[內容]` `[次數]`**：瘋狂轟炸某人。\n"
        "* **/停止標註**：結束目前的轟炸。\n"
        "* **/狀態**：查看目前掛機頻道、已掛機時間與延遲。\n"
        "* **/使用方式**：顯示此幫助選單。"
    )

# --- [工具] 更新統計頻道邏輯 ---
async def update_stats_logic(guild):
    if guild.id not in stats_channels: return
    channels = stats_channels[guild.id]
    total = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    mapping = {"total": f"全部: {total}", "members": f"Members: {total - bots}", "bots": f"Bots: {bots}"}
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
    activity = discord.Activity(type=discord.ActivityType.custom, name=".", state="慢慢摸索中", details="正在玩 你的感情")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    print(f"機器人已上線：{bot.user}")
    if not check_connection.is_running(): check_connection.start()
    if not tagging_task.is_running(): tagging_task.start()
    if not update_member_stats.is_running(): update_member_stats.start()

@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user and bot.user.mentioned_in(message): await message.channel.send(get_usage_text())
    await bot.process_commands(message)

# ===== 歡迎訊息邏輯 (保留原樣) =====
@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if channel is not None:
        total_members = member.guild.member_count
        embed = discord.Embed(title=f"你好 歡迎加入 {member.guild.name}", description=f"{member.mention}", color=discord.Color.from_rgb(255, 105, 180))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"你是本伺服器的第 {total_members} 位成員")
        await channel.send(embed=embed)
    await update_stats_logic(member.guild)

@bot.event
async def on_member_remove(member):
    await update_stats_logic(member.guild)

# ===== 循環任務 =====
@tasks.loop(seconds=30)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        channel = bot.get_channel(cid)
        if channel:
            try: await channel.connect(self_deaf=True, self_mute=False)
            except: pass

@tasks.loop(seconds=0.8)
async def tagging_task():
    for gid, data in list(tag_targets.items()):
        channel = bot.get_channel(data["channel_id"])
        if not channel: continue
        try:
            user_mention = f"<@{data['user_id']}>"
            await channel.send(f"{user_mention} {data['content']}")
            if data["count"] is not None:
                data["count"] -= 1
                if data["count"] <= 0: tag_targets.pop(gid)
        except discord.errors.HTTPException as e:
            if e.status == 429: await asyncio.sleep(3)
        except: pass

# ===== Slash Commands =====

@tree.command(name="設定統計頻道", description="建立顯示伺服器人數的統計頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def setup_stats(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False), guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True)}
    try:
        category = await guild.create_category(" 伺服器數據", position=0)
        total = guild.member_count
        bots = sum(1 for m in guild.members if m.bot)
        c_total = await guild.create_voice_channel(f"全部: {total}", category=category, overwrites=overwrites)
        c_members = await guild.create_voice_channel(f"人類: {total - bots}", category=category, overwrites=overwrites)
        c_bots = await guild.create_voice_channel(f"Bots: {bots}", category=category, overwrites=overwrites)
        stats_channels[guild.id] = {"total": c_total.id, "members": c_members.id, "bots": c_bots.id}
        await interaction.followup.send("統計頻道與 Embed 歡迎功能已準備就緒！")
    except Exception as e: await interaction.followup.send(f"建立失敗：{e}")

@tree.command(name="使用方式", description="顯示機器人的指令列表與詳細用法")
async def usage(interaction: discord.Interaction):
    await interaction.response.send_message(get_usage_text())

@tree.command(name="加入", description="讓機器人進入語音頻道掛機")
async def join(interaction: discord.Interaction, channel: discord.VoiceChannel | None = None):
    await interaction.response.defer(thinking=True)
    channel = channel or getattr(interaction.user.voice, 'channel', None)
    if not channel: return await interaction.followup.send("你沒選頻道也沒在語音頻道 我要進哪", ephemeral=True)
    if interaction.guild.voice_client: await interaction.guild.voice_client.move_to(channel)
    else: await channel.connect(self_deaf=True, self_mute=False)
    stay_channels[interaction.guild.id] = channel.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.followup.send(f"我進來 **{channel.name}** 竊聽了")

@tree.command(name="離開", description="讓機器人離開語音頻道")
async def leave(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        stay_since.pop(interaction.guild.id, None)
        queues.pop(interaction.guild.id, None)
        await interaction.followup.send("我走了 你別再難過")
    else: await interaction.followup.send("我不在語音頻道 要離開去哪 ", ephemeral=True)

# ===== 升級後的播放功能 =====
@tree.command(name="播放", description="直接上傳音檔 (mp3, ogg, m4a) 進行播放")
async def play_file(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer(thinking=True)
    if not any(檔案.filename.lower().endswith(i) for i in ['.mp3', '.ogg', '.m4a', '.wav']):
        return await interaction.followup.send("格式不支援！請上傳音檔。", ephemeral=True)

    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]

    if not interaction.user.voice: return await interaction.followup.send("你必須先進入一個語音頻道", ephemeral=True)
    
    if not interaction.guild.voice_client:
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True, self_mute=False)
        stay_channels[gid] = interaction.user.voice.channel.id
        stay_since[gid] = time.time()
    else:
        mgr.vc = interaction.guild.voice_client

    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing() and not mgr.vc.is_paused(): mgr.play_next()

    embed = discord.Embed(title="音樂播放中", description=f"正在播放：**{檔案.filename}**", color=0xaa96da)
    embed.set_footer(text=f"模式: {mgr.mode} | 音量: {int(mgr.volume*100)}%")
    await interaction.followup.send(embed=embed, view=MusicControlView(mgr))

@tree.command(name="停止播放", description="停止目前播放的音檔")
async def stop_audio(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("已停止播放。")
    else: await interaction.response.send_message("目前沒有正在播放的音檔。", ephemeral=True)

# =========================================================
# ===== 修復：開始標註指令 (避免 Unknown interaction) =====
# =========================================================
@tree.command(name="開始標註", description="瘋狂標註某人")
async def start_tag(interaction: discord.Interaction, target: discord.Member, 內容: str, 次數: int | None = None):
    # 【修正】加入 defer 處理延遲
    await interaction.response.defer(thinking=True)
    
    tag_targets[interaction.guild.id] = {
        "user_id": target.id, 
        "content": 內容, 
        "channel_id": interaction.channel_id, 
        "count": 次數
    }
    
    # 【修正】使用 followup 發送
    await interaction.followup.send(f"開始轟炸 {target.mention}！內容：{內容}")

@tree.command(name="停止標註", description="停止目前的標註任務")
async def stop_tag(interaction: discord.Interaction):
    if interaction.guild_id in tag_targets:
        tag_targets.pop(interaction.guild_id)
        await interaction.response.send_message("已停止轟炸 饒他一命。")
    else: await interaction.response.send_message("目前沒有正在進行的轟炸任務。", ephemeral=True)

@tree.command(name="狀態", description="檢查掛機與延遲狀態")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    if interaction.guild_id not in stay_channels: return await interaction.followup.send("老子沒掛在任何語音頻道", ephemeral=True)
    channel = bot.get_channel(stay_channels[interaction.guild_id])
    duration = format_duration(int(time.time() - stay_since.get(interaction.guild_id, time.time())))
    await interaction.followup.send(f"目前在 **{channel.name if channel else '未知'}** 竊聽中\n已竊聽 **{duration}**\n延遲：{round(bot.latency * 1000)} ms", ephemeral=True)

@tree.command(name="延遲", description="檢查機器人延遲")
async def latency(interaction: discord.Interaction):
    await interaction.response.send_message(f"本公子的延遲為: {round(bot.latency * 1000)} ms", ephemeral=True)

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
