import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import aiohttp
import psutil
import static_ffmpeg
import requests
from server import keep_alive

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
queues = {} 

# ===== 播放音檔設定 =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# =========================================================
# ===== 音樂管理系統 =====
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

    def get_status_embed(self):
        status = "播放中" if self.vc and self.vc.is_playing() else "已暫停"
        loop_map = {"none": "關閉", "single": "單曲", "all": "全清單"}
        
        embed = discord.Embed(title="音樂控制面板", color=0xaa96da)
        embed.add_field(name="當前歌曲", value=self.current[1] if self.current else "無", inline=False)
        embed.add_field(name="狀態", value=status, inline=True)
        embed.add_field(name="循環模式", value=loop_map.get(self.mode, "關閉"), inline=True)
        embed.add_field(name="當前音量", value=f"{int(self.volume*100)}%", inline=True)
        
        q_len = len(self.queue)
        embed.set_footer(text=f"待播清單剩餘: {q_len} 首歌曲")
        return embed

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
        self.vc.play(source, after=lambda e: bot.loop.call_soon_threadsafe(self.play_next, e))

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
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.manager.vc.is_playing():
            self.manager.vc.pause()
        elif self.manager.vc.is_paused():
            self.manager.vc.resume()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="下一首", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.queue:
            return await interaction.response.send_message("待播清單已空", ephemeral=True)
        self.manager.vc.stop()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="音量+", style=discord.ButtonStyle.gray)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = min(self.manager.volume + 0.1, 2.0)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

    @discord.ui.button(label="音量-", style=discord.ButtonStyle.gray)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.manager.volume = max(self.manager.volume - 0.1, 0.0)
        if self.manager.vc.source: self.manager.vc.source.volume = self.manager.volume
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

# ===== 系統工具函式 =====
async def get_system_info():
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_used = round(ram.used / (1024 ** 3), 2)
    ram_total = round(ram.total / (1024 ** 3), 2)
    net_io = psutil.net_io_counters()
    sent = round(net_io.bytes_sent / (1024 ** 3), 2)
    recv = round(net_io.bytes_recv / (1024 ** 3), 2)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get('https://api.ipify.org', timeout=5) as resp:
                ip = await resp.text()
        except:
            ip = "無法獲取"
            
    return {
        "cpu": f"{cpu_usage}%",
        "ram": f"{ram_used} GB / {ram_total} GB",
        "ip": ip,
        "net": f"上傳 {sent} GB | 下載 {recv} GB"
    }

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
        "本機器人為 24/7 語音掛機 設計 具備30秒自動重連機制。\n\n"
        "### 指令列表\n"
        "* /加入 [頻道]：讓機器人進入語音頻道（可不選，預設進入你所在的頻道）。\n"
        "* /設定統計頻道：建立自動更新人數的統計頻道。\n"
        "* /播放 [檔案]：直接上傳 mp3, ogg, m4a 檔案進行播放。\n"
        "* /系統狀態：查看硬體資訊。\n"
        "* /停止播放：停止目前播放的音檔。\n"
        "* /離開：讓機器人退出語音頻道並停止掛機。\n"
        "* /開始標註 [成員] [內容] [次數]：瘋狂轟炸某人。\n"
        "* /停止標註：結束目前的轟炸。\n"
        "* /狀態：查看目前掛機頻道、已掛機時間與延遲。\n"
        "* /移除身分組 [成員] [身分組]：將某人的身分組拔掉。\n"
        "* /給予身分組 [成員] [身分組]：給予某人特定的身分組。\n"
        "* /查看審核日誌 [筆數]：查看伺服器最近的操作紀錄。\n"
        "* /使用方式：顯示此幫助選單。"
    )

async def update_stats_logic(guild):
    if guild.id not in stats_channels: return
    channels = stats_channels[guild.id]
    total = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    mapping = {"total": f"全部: {total}", "members": f"成員: {total - bots}", "bots": f"機器人: {bots}"}
    for key, new_name in mapping.items():
        channel = bot.get_channel(channels.get(key))
        if channel and channel.name != new_name:
            try: await channel.edit(name=new_name)
            except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds: await update_stats_logic(guild)

@bot.event
async def on_ready():
    await tree.sync()
    activity = discord.Activity(type=discord.ActivityType.custom, name=".", state="正在運作", details="系統正常")
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

@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if channel is not None:
        total_members = member.guild.member_count
        embed = discord.Embed(title=f"歡迎加入 {member.guild.name}", description=f"{member.mention}", color=discord.Color.from_rgb(255, 105, 180))
        embed.set_footer(text=f"你是本伺服器的第 {total_members} 位成員")
        await channel.send(embed=embed)
    await update_stats_logic(member.guild)

@bot.event
async def on_member_remove(member):
    await update_stats_logic(member.guild)

@tasks.loop(seconds=30)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        channel = bot.get_channel(cid)
        if channel:
            try: await channel.connect(self_deaf=True, self_mute=False)
            except: pass

@tasks.loop(seconds=1.5)
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
            if e.status == 429: await asyncio.sleep(5)
        except: pass

# ===== Slash Commands =====

@tree.command(name="移除身分組", description="移除指定成員的身分組")
@app_commands.describe(target="目標成員", role="要移除的身分組")
async def remove_role(interaction: discord.Interaction, target: discord.Member, role: discord.Role):
    await interaction.response.defer(thinking=True)
    ALLOWED_ROLE_ID = 0 
    has_perm = (
        interaction.user.guild_permissions.manage_roles or 
        any(r.id == ALLOWED_ROLE_ID for r in interaction.user.roles)
    )
    if not has_perm:
        return await interaction.followup.send("錯誤：你沒有權限執行此操作。", ephemeral=True)
    if role >= interaction.guild.me.top_role:
        return await interaction.followup.send("錯誤：我的權限低於該身分組，無法移除。", ephemeral=True)
    if role not in target.roles:
        return await interaction.followup.send(f"錯誤：成員 **{target.display_name}** 目前沒有 **{role.name}** 身分組。", ephemeral=True)
    try:
        await target.remove_roles(role)
        await interaction.followup.send(f"成功移除 **{target.display_name}** 的 **{role.name}** 身分組。")
    except Exception as e:
        await interaction.followup.send(f"移除失敗: {e}", ephemeral=True)

@tree.command(name="給予身分組", description="給予指定成員身分組")
@app_commands.describe(target="目標成員", role="要給予的身分組")
async def add_role(interaction: discord.Interaction, target: discord.Member, role: discord.Role):
    await interaction.response.defer(thinking=True)
    ALLOWED_ROLE_ID = 0 
    has_perm = (
        interaction.user.guild_permissions.manage_roles or 
        any(r.id == ALLOWED_ROLE_ID for r in interaction.user.roles)
    )
    if not has_perm:
        return await interaction.followup.send("錯誤：你沒有權限執行此操作。", ephemeral=True)
    if role >= interaction.guild.me.top_role:
        return await interaction.followup.send("錯誤：我的權限低於該身分組，無法給予。", ephemeral=True)
    if role in target.roles:
        return await interaction.followup.send(f"錯誤：成員 **{target.display_name}** 已經擁有 **{role.name}** 身分組。", ephemeral=True)
    try:
        await target.add_roles(role)
        await interaction.followup.send(f"成功給予 **{target.display_name}** **{role.name}** 身分組。")
    except Exception as e:
        await interaction.followup.send(f"給予失敗: {e}", ephemeral=True)

@tree.command(name="查看審核日誌", description="查看最近的審核日誌紀錄")
@app_commands.describe(limit="讀取筆數 (預設5筆)")
async def view_audit_logs(interaction: discord.Interaction, limit: int = 5):
    await interaction.response.defer(thinking=True)
    if not interaction.user.guild_permissions.view_audit_log:
        return await interaction.followup.send("錯誤：你沒有查看審核日誌的權限。", ephemeral=True)
    
    # 動作翻譯對照表
    action_map = {
        discord.AuditLogAction.member_role_update: "更動成員身分組",
        discord.AuditLogAction.member_move: "移動成員頻道",
        discord.AuditLogAction.role_create: "創建身分組",
        discord.AuditLogAction.role_delete: "刪除身分組",
        discord.AuditLogAction.role_update: "更新身分組設定",
        discord.AuditLogAction.kick: "踢出成員",
        discord.AuditLogAction.ban: "封鎖成員",
        discord.AuditLogAction.unban: "解除封鎖",
        discord.AuditLogAction.bot_add: "新增機器人",
        discord.AuditLogAction.invite_create: "建立邀請碼",
        discord.AuditLogAction.member_update: "更新成員資料"
    }

    log_text = "### 最近審核日誌\n"
    try:
        async for entry in interaction.guild.audit_logs(limit=limit):
            action_name = action_map.get(entry.action, str(entry.action))
            
            target_display = entry.target
            if hasattr(entry.target, "name"):
                target_display = entry.target.name
            elif isinstance(entry.target, discord.Object):
                target_display = f"未知目標 (ID: {entry.target.id})"

            log_text += f"* 執行者: **{entry.user}** | 動作: {action_name} | 目標: {target_display}\n"
        
        await interaction.followup.send(log_text)
    except Exception as e:
        await interaction.followup.send(f"讀取失敗: {e}", ephemeral=True)

@tree.command(name="系統狀態", description="查看機器人伺服器硬體負載與網路資訊")
async def system_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    info = await get_system_info()
    embed = discord.Embed(title="伺服器硬體狀態", color=0x3498db)
    embed.add_field(name="CPU 使用率", value=info["cpu"], inline=True)
    embed.add_field(name="記憶體使用 (RAM)", value=info["ram"], inline=True)
    embed.add_field(name="網路總流量 (總計)", value=info["net"], inline=False)
    embed.add_field(name="IP 位址", value=info["ip"], inline=False)
    latency = f"{round(bot.latency * 1000)} ms"
    embed.add_field(name="指令延遲", value=latency, inline=True)
    await interaction.followup.send(embed=embed)

@tree.command(name="設定統計頻道", description="建立顯示伺服器人數的統計頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def setup_stats(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False), guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True)}
    try:
        category = await guild.create_category("伺服器數據", position=0)
        total = guild.member_count
        bots = sum(1 for m in guild.members if m.bot)
        c_total = await guild.create_voice_channel(f"全部: {total}", category=category, overwrites=overwrites)
        c_members = await guild.create_voice_channel(f"成員: {total - bots}", category=category, overwrites=overwrites)
        c_bots = await guild.create_voice_channel(f"機器人: {bots}", category=category, overwrites=overwrites)
        stats_channels[guild.id] = {"total": c_total.id, "members": c_members.id, "bots": c_bots.id}
        await interaction.followup.send("統計頻道建立完成")
    except Exception as e: await interaction.followup.send(f"建立失敗：{e}")

@tree.command(name="使用方式", description="顯示機器人的指令列表與詳細用法")
async def usage(interaction: discord.Interaction):
    await interaction.response.send_message(get_usage_text())

@tree.command(name="加入", description="讓機器人進入語音頻道掛機")
async def join(interaction: discord.Interaction, channel: discord.VoiceChannel | None = None):
    await interaction.response.defer(thinking=True)
    channel = channel or getattr(interaction.user.voice, 'channel', None)
    if not channel: return await interaction.followup.send("未找到語音頻道", ephemeral=True)
    if interaction.guild.voice_client: await interaction.guild.voice_client.move_to(channel)
    else: await channel.connect(self_deaf=True, self_mute=False)
    stay_channels[interaction.guild.id] = channel.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.followup.send(f"我進來: {channel.name} 竊聽了")

@tree.command(name="離開", description="讓機器人離開語音頻道")
async def leave(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        stay_since.pop(interaction.guild.id, None)
        queues.pop(interaction.guild.id, None)
        await interaction.followup.send("我走了")
    else: await interaction.followup.send("機器人不在語音頻道中", ephemeral=True)

@tree.command(name="播放", description="直接上傳音檔 (mp3, ogg, m4a) 進行播放")
async def play_file(interaction: discord.Interaction, 檔案: discord.Attachment):
    await interaction.response.defer(thinking=True)
    if not any(檔案.filename.lower().endswith(i) for i in ['.mp3', '.ogg', '.m4a', '.wav']):
        return await interaction.followup.send("格式不支援", ephemeral=True)
    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]
    if not interaction.user.voice: return await interaction.followup.send("請先進入語音頻道", ephemeral=True)
    if not interaction.guild.voice_client:
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True, self_mute=False)
        stay_channels[gid] = interaction.user.voice.channel.id
        stay_since[gid] = time.time()
    else:
        mgr.vc = interaction.guild.voice_client
    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing() and not mgr.vc.is_paused(): mgr.play_next()
    await interaction.followup.send(embed=mgr.get_status_embed(), view=MusicControlView(mgr))

@tree.command(name="停止播放", description="停止目前播放的音檔")
async def stop_audio(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("停止播放")
    else: await interaction.response.send_message("沒有正在播放的音檔", ephemeral=True)

@tree.command(name="開始標註", description="瘋狂轟炸某人")
async def start_tag(interaction: discord.Interaction, target: discord.Member, 內容: str, 次數: int | None = None):
    await interaction.response.defer(thinking=True)
    tag_targets[interaction.guild.id] = {
        "user_id": target.id, 
        "content": 內容, 
        "channel_id": interaction.channel_id, 
        "count": 次數
    }
    await interaction.followup.send(f"開始標註 {target.mention}")

@tree.command(name="停止標註", description="停止目前的標註任務")
async def stop_tag(interaction: discord.Interaction):
    if interaction.guild_id in tag_targets:
        tag_targets.pop(interaction.guild_id)
        await interaction.response.send_message("已停止標註")
    else: await interaction.response.send_message("沒有正在進行的標註任務", ephemeral=True)

@tree.command(name="狀態", description="檢查掛機與延遲狀態")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    if interaction.guild_id not in stay_channels: return await interaction.followup.send("機器人未在掛機狀態", ephemeral=True)
    channel = bot.get_channel(stay_channels[interaction.guild_id])
    duration = format_duration(int(time.time() - stay_since.get(interaction.guild_id, time.time())))
    await interaction.followup.send(f"當前頻道: {channel.name if channel else '未知'}\n掛機時間: {duration}\n延遲: {round(bot.latency * 1000)} ms", ephemeral=True)

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)
