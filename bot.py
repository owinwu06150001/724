import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import time
import asyncio
import datetime
import psutil
import static_ffmpeg
from server import keep_alive

# 初始化 FFMPEG
static_ffmpeg.add_paths()

# ===== 啟動 Web 服務 =====
keep_alive()

# ===== Intents 設定 =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== 成員加入歡迎卡片 =====
@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if not channel:
        return

    embed = discord.Embed(
        description=f"你好 歡迎加入 {member.guild.name}\n\n{member.mention}\n\n你是本伺服器的第 {member.guild.member_count} 位成員",
        color=0x2b2d31
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    await channel.send(embed=embed)

# ===== 資料儲存 =====
stay_channels = {}
stay_since = {}
tag_targets = {}
stats_channels = {}
queues = {} 

# ===== 擴充後的不雅語言詞庫 =====
COMMON_PROFANITY = [
    "幹", "靠", "屁", "垃圾", "智障", "腦癱", "死全家", "孤兒", 
    "廢物", "去死", "操你媽", "你媽死了", "尼哥", "畜生", "雜種", 
    "低能兒", "白癡", "腦殘", "傻逼", "機掰", "雞掰", "賤人", "賤貨",
    "操", "肏", "幹你娘", "靠北", "靠腰", "三小", "幹林娘", "機歪",
    "支那", "下流", "無恥", "欠幹", "狗娘養的", "尼瑪"
]

# 不雅語言偵測設定
filter_config = {
    "enabled": False,
    "log_channel_id": None,
    "keywords": COMMON_PROFANITY
}

# ===== 播放音檔設定 =====
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# ===== 審核日誌對照表 =====
AUDIT_LOG_ACTIONS_CN = {
    "guild_update": "更新伺服器", "channel_create": "建立頻道", "channel_update": "更新頻道",
    "channel_delete": "刪除頻道", "member_kick": "踢出成員", "member_ban": "封鎖成員",
    "member_unban": "解除封鎖", "member_update": "更新成員", "member_role_update": "更新成員身分組",
    "role_create": "建立身分組", "role_update": "更新身分組", "role_delete": "刪除身分組",
    "message_delete": "刪除訊息", "message_bulk_delete": "批量刪除訊息",
}

# ===== 公用手冊內容 (無表情符號) =====
def get_help_text(bot_mention):
    return (
        f"## {bot_mention} 使用手冊\n"
        "本機器人為 24/7 語音掛機設計 具備30秒自動重連機制。\n\n"
        "### 指令列表\n"
        "* /加入 [頻道]：進入語音頻道掛機。\n"
        "* /設定統計頻道：建立自動更新人數的統計頻道。\n"
        "* /播放 [檔案]：上傳音檔（mp3, ogg, m4a）播放。\n"
        "* /系統狀態：查看硬體資訊。\n"
        "* /停止播放：中斷目前的音樂。\n"
        "* /離開：退出頻道並停止掛機。\n"
        "* /開始標註 [成員] [內容] [次數]：執行標註轟炸。\n"
        "* /停止標註：結束轟炸。\n"
        "* /設定過濾器：開啟/關閉不雅語言禁言系統。\n"
        "* /新增過濾詞彙：手動加入關鍵字。\n"
        "* /狀態：查看掛機時間與延遲。\n"
        "* /移除身分組 / /給予身分組：管理成員權限。\n"
        "* /查看審核日誌：查看操作紀錄。\n"
        "* /使用方式：顯示本手冊。"
    )

# =========================================================
# ===== 核心邏輯 (轟炸與音樂管理類別) =====
# =========================================================
async def tag_logic(channel, target, content, times):
    for i in range(times):
        if tag_targets.get(target.id) is False:
            break
        try:
            await channel.send(f"{target.mention} {content}")
        except:
            break
        await asyncio.sleep(0.8)

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
        loop_map = {"none": "不循環", "single": "單曲循環", "all": "歌單循環"}
        embed = discord.Embed(title="音樂控制面板", color=0xaa96da)
        embed.add_field(name="當前歌曲", value=self.current[1] if self.current else "無", inline=False)
        embed.add_field(name="狀態", value=status, inline=True)
        embed.add_field(name="循環模式", value=loop_map.get(self.mode), inline=True)
        embed.add_field(name="當前音量", value=f"{int(self.volume*100)}%", inline=True)
        embed.set_footer(text=f"待播清單剩餘: {len(self.queue)} 首歌曲")
        return embed

    def play_next(self, error=None):
        if not self.vc or not self.vc.is_connected(): return
        if self.current:
            if self.mode == "single": self.queue.insert(0, self.current)
            elif self.mode == "all": self.queue.append(self.current)
            else: self.history.append(self.current)
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

    @discord.ui.button(label="暫停/繼續", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.manager.vc: return
        if self.manager.vc.is_playing(): self.manager.vc.pause()
        elif self.manager.vc.is_paused(): self.manager.vc.resume()
        await interaction.response.edit_message(embed=self.manager.get_status_embed(), view=self)

# =========================================================
# ===== 事件監聽 (過濾器與標註手冊) =====
# =========================================================
@bot.event
async def on_message(message):
    if message.author.bot: return

    if bot.user.mentioned_in(message) and message.mention_everyone is False:
        await message.channel.send(get_help_text(bot.user.mention))

    if filter_config["enabled"]:
        if any(word in message.content for word in filter_config["keywords"]):
            try:
                msg_text = message.content
                user = message.author
                await message.delete()
                await user.timeout(datetime.timedelta(seconds=60), reason="使用不雅詞彙")
                
                if filter_config["log_channel_id"]:
                    log_ch = bot.get_channel(filter_config["log_channel_id"])
                    if log_ch:
                        log_embed = discord.Embed(title="違規紀錄", color=0xff0000)
                        log_embed.add_field(name="用戶", value=user.mention)
                        log_embed.add_field(name="違規內容", value=msg_text)
                        await log_ch.send(embed=log_embed)
            except: pass

    await bot.process_commands(message)

@bot.event
async def on_ready():
    await tree.sync()
    update_member_stats.start()
    check_connection.start()

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name="24/7 掛機中")
    )

    print(f"機器人已啟動：{bot.user}")


# =========================================================
# ===== 中文指令區 =====
# =========================================================
# ===== 成員管理功能 =====

@tree.command(name="踢出", description="踢出指定成員")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.describe(成員="要踢出的成員", 原因="踢出原因")
async def kick_member(interaction: discord.Interaction, 成員: discord.Member, 原因: str = "無"):
    if 成員.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("你無法踢出此成員（權限不足）", ephemeral=True)

    try:
        await 成員.kick(reason=原因)
        await interaction.response.send_message(f"已踢出 {成員.mention}\n原因: {原因}")
    except Exception as e:
        await interaction.response.send_message(f"踢出失敗: {e}", ephemeral=True)


@tree.command(name="封鎖", description="封鎖指定成員")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(成員="要封鎖的成員", 原因="封鎖原因")
async def ban_member(interaction: discord.Interaction, 成員: discord.Member, 原因: str = "無"):
    if 成員.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("你無法封鎖此成員（權限不足）", ephemeral=True)

    try:
        await 成員.ban(reason=原因)
        await interaction.response.send_message(f"已封鎖 {成員.mention}\n原因: {原因}")
    except Exception as e:
        await interaction.response.send_message(f"封鎖失敗: {e}", ephemeral=True)


@tree.command(name="解除封鎖", description="解除封鎖成員")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user_id="要解除封鎖的用戶ID")
async def unban_member(interaction: discord.Interaction, user_id: str):
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"已解除封鎖 {user}")
    except Exception as e:
        await interaction.response.send_message(f"解除封鎖失敗: {e}", ephemeral=True)



@tree.command(name="禁言", description="將成員禁言指定時間")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(成員="要禁言的成員", 時間="禁言時間 (例如: 1d2h30m10s)", 原因="禁言原因")
async def timeout_member(interaction: discord.Interaction, 成員: discord.Member, 時間: str, 原因: str = "無"):
    if 成員.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("你無法禁言此成員（權限不足）", ephemeral=True)

    秒數 = parse_duration(時間)
    if 秒數 is None or 秒數 <= 0:
        return await interaction.response.send_message("時間格式錯誤，請使用 1d2h30m10s 的格式", ephemeral=True)

    try:
        duration = datetime.timedelta(seconds=秒數)
        await 成員.timeout(duration, reason=原因)
        await interaction.response.send_message(f"已將 {成員.mention} 禁言 {時間}\n原因: {原因}")
    except Exception as e:
        await interaction.response.send_message(f"禁言失敗: {e}", ephemeral=True)


@tree.command(name="解除禁言", description="解除成員禁言")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(成員="要解除禁言的成員")
async def remove_timeout(interaction: discord.Interaction, 成員: discord.Member):
    try:
        await 成員.timeout(None)
        await interaction.response.send_message(f"已解除 {成員.mention} 的禁言")
    except Exception as e:
        await interaction.response.send_message(f"解除禁言失敗: {e}", ephemeral=True)


@tree.command(name="使用方式", description="顯示功能清單")
async def show_help(interaction: discord.Interaction):
    await interaction.response.send_message(get_help_text(bot.user.mention))

@tree.command(name="設定過濾器", description="開啟/關閉禁言系統")
@app_commands.describe(開啟="是否啟動", 記錄頻道="違規訊息日誌頻道")
@app_commands.checks.has_permissions(manage_guild=True)
async def filter_set(interaction: discord.Interaction, 開啟: bool, 記錄頻道: discord.TextChannel):
    filter_config["enabled"] = 開啟
    filter_config["log_channel_id"] = 記錄頻道.id
    status = "開啟" if 開啟 else "關閉"
    await interaction.response.send_message(f"過濾系統：{status}，日誌頻道：{記錄頻道.mention}")

@tree.command(name="新增過濾詞彙", description="加入新的禁止字詞")
@app_commands.describe(詞彙="要禁用的字詞")
@app_commands.checks.has_permissions(manage_guild=True)
async def add_profanity(interaction: discord.Interaction, 詞彙: str):
    if 詞彙 not in filter_config["keywords"]:
        filter_config["keywords"].append(詞彙)
        await interaction.response.send_message(f"已將「{詞彙}」加入過濾名單")
    else:
        await interaction.response.send_message("該詞彙已在名單中")

@tree.command(name="開始標註", description="對成員執行轟炸")
async def start_bomb(interaction: discord.Interaction, 成員: discord.Member, 內容: str, 次數: int):
    if 次數 <= 0: return await interaction.response.send_message("次數必須大於0", ephemeral=True)
    tag_targets[成員.id] = True
    await interaction.response.send_message(f"開始轟炸 {成員.mention}")
    await tag_logic(interaction.channel, 成員, 內容, 次數)
    tag_targets[成員.id] = False

@tree.command(name="停止標註", description="停止轟炸")
async def stop_bomb(interaction: discord.Interaction, 成員: discord.Member):
    tag_targets[成員.id] = False
    await interaction.response.send_message(f"已停止對 {成員.mention} 的轟炸")

@tree.command(name="加入", description="進入語音頻道掛機")
async def join_vc(interaction: discord.Interaction, 頻道: discord.VoiceChannel = None):
    頻道 = 頻道 or (interaction.user.voice.channel if interaction.user.voice else None)
    if not 頻道: return await interaction.response.send_message("請先進入頻道或指定頻道", ephemeral=True)
    await 頻道.connect(self_deaf=True)
    stay_channels[interaction.guild.id] = 頻道.id
    stay_since[interaction.guild.id] = time.time()
    await interaction.response.send_message(f"我進來 {頻道.name} 竊聽了")

@tree.command(name="播放", description="播放上傳的音檔")
async def play_audio(interaction: discord.Interaction, 檔案: discord.Attachment):
    if not 檔案.filename.endswith(('.mp3', '.ogg', '.m4a')):
        return await interaction.response.send_message("格式不支援", ephemeral=True)
    
    await interaction.response.defer(thinking=True)
    gid = interaction.guild_id
    if gid not in queues: queues[gid] = MusicManager(gid)
    mgr = queues[gid]
    
    if not interaction.guild.voice_client:
        if not interaction.user.voice: return await interaction.followup.send("請先進入語音")
        mgr.vc = await interaction.user.voice.channel.connect(self_deaf=True)
    else: mgr.vc = interaction.guild.voice_client

    mgr.queue.append((檔案.url, 檔案.filename))
    if not mgr.vc.is_playing() and not mgr.vc.is_paused(): mgr.play_next()
    await interaction.followup.send(embed=mgr.get_status_embed(), view=MusicControlView(mgr))

@tree.command(name="設定統計頻道", description="建立人數統計頻道")
@app_commands.checks.has_permissions(manage_channels=True)
async def stats_setup(interaction: discord.Interaction):
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True),
        guild.me: discord.PermissionOverwrite(connect=True, view_channel=True, manage_channels=True)
    }
    
    category = await guild.create_category("伺服器數據", position=0, overwrites=overwrites)
    
    total = guild.member_count
    bots = len([m for m in guild.members if m.bot])
    humans = total - bots
    online = len([m for m in guild.members if m.status != discord.Status.offline])
    
    c_total = await guild.create_voice_channel(f"全部人數: {total}", category=category, overwrites=overwrites)
    c_humans = await guild.create_voice_channel(f"成員人數: {humans}", category=category, overwrites=overwrites)
    c_online = await guild.create_voice_channel(f"在線成員: {online}", category=category, overwrites=overwrites)
    c_bots = await guild.create_voice_channel(f"機器人: {bots}", category=category, overwrites=overwrites)
    
    stats_channels[guild.id] = {
        "total": c_total.id,
        "humans": c_humans.id,
        "online": c_online.id,
        "bots": c_bots.id
    }
    await interaction.response.send_message("統計頻道建立完成")

@tree.command(name="給予身分組", description="賦予成員身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_add(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    try:
        await 成員.add_roles(身分組)
        await interaction.response.send_message(f"已將 {身分組.name} 給予 {成員.display_name}")
    except Exception as e:
        await interaction.response.send_message(f"失敗: {e}")

@tree.command(name="移除身分組", description="移除成員的身分組")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_rem(interaction: discord.Interaction, 成員: discord.Member, 身分組: discord.Role):
    try:
        await 成員.remove_roles(身分組)
        await interaction.response.send_message(f"已從 {成員.display_name} 移除 {身分組.name}")
    except Exception as e:
        await interaction.response.send_message(f"失敗: {e}")

@tree.command(name="系統狀態", description="硬體監控")
async def sys_info(interaction: discord.Interaction):
    await interaction.response.send_message(f"CPU: {psutil.cpu_percent()}% | RAM: {psutil.virtual_memory().percent}%")

@tree.command(name="離開", description="退出語音")
async def leave_vc(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        stay_channels.pop(interaction.guild.id, None)
        await interaction.response.send_message("我走了")
    else: await interaction.response.send_message("沒在任何語音頻道裡我是要離開去哪")

@tree.command(name="狀態", description="查看掛機時間與延遲")
async def status_info(interaction: discord.Interaction):
    if interaction.guild_id not in stay_channels: return await interaction.response.send_message("未在掛機狀態", ephemeral=True)
    uptime = int(time.time() - stay_since.get(interaction.guild_id, time.time()))
    await interaction.response.send_message(f"掛機時間: {uptime} 秒 | 延遲: {round(bot.latency * 1000)} ms")

@tree.command(name="查看審核日誌", description="查看操作紀錄")
@app_commands.describe(筆數="顯示數量(1-20)")
@app_commands.checks.has_permissions(view_audit_log=True)
async def show_logs(interaction: discord.Interaction, 筆數: int = 5):
    await interaction.response.defer(thinking=True)
    筆數 = min(max(筆數, 1), 20)
    log_text = f"### 最近的 {筆數} 筆審核日誌\n"
    async for entry in interaction.guild.audit_logs(limit=筆數):
        raw_action = str(entry.action).split('.')[-1]
        action_cn = AUDIT_LOG_ACTIONS_CN.get(raw_action, raw_action)
        log_text += f"* 時間: {entry.created_at.strftime('%Y-%m-%d %H:%M:%S')} | 執行者: {entry.user} | 動作: {action_cn} | 目標: {entry.target}\n"
    await interaction.followup.send(log_text)

# ===== 背景任務 =====
@tasks.loop(seconds=30)
async def check_connection():
    for gid, cid in list(stay_channels.items()):
        guild = bot.get_guild(gid)
        if not guild or (guild.voice_client and guild.voice_client.is_connected()): continue
        ch = bot.get_channel(cid)
        if ch: 
            try: await ch.connect(self_deaf=True)
            except: pass

@tasks.loop(minutes=10)
async def update_member_stats():
    for guild in bot.guilds:
        if guild.id in stats_channels:
            stats = stats_channels[guild.id]
            total = guild.member_count
            bots = len([m for m in guild.members if m.bot])
            humans = total - bots
            online = len([m for m in guild.members if m.status != discord.Status.offline])
            
            data_map = {
                "total": f"全部人數: {total}",
                "humans": f"成員人數: {humans}",
                "online": f"在線成員: {online}",
                "bots": f"機器人: {bots}"
            }
            
            for key, name in data_map.items():
                ch = bot.get_channel(stats.get(key))
                if ch:
                    try: await ch.edit(name=name)
                    except: pass
                        

token = os.environ.get("DISCORD_TOKEN")
if token: bot.run(token)






