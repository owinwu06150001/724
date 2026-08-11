import os
import asyncio
import logging
import datetime
import psutil
import aiohttp
import discord

logger = logging.getLogger('discord')

class TelegramBotHandler:
    def __init__(self, discord_bot, start_time):
        self.bot = discord_bot
        self.start_time = start_time
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.admin_id = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()
        self.offset = 0
        self.api_url = f"https://api.telegram.org/bot{self.token}"

    def is_admin(self, user_id):
        if not self.admin_id:
            return True
        return str(user_id) == str(self.admin_id)

    async def send_message(self, chat_id, text, parse_mode="Markdown"):
        if not self.token:
            return
        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"Telegram 訊息發送失敗: {await resp.text()}")
        except Exception as e:
            logger.error(f"Telegram 發送異常: {e}")

    async def start_polling(self):
        if not self.token:
            logger.info("未設定 TELEGRAM_BOT_TOKEN，Telegram 機器人功能停用。")
            return

        logger.info("Telegram 機器人服務已啟動並開始監聽指令...")
        
        while not self.bot.is_closed():
            try:
                url = f"{self.api_url}/getUpdates?offset={self.offset}&timeout=30"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=35) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("ok"):
                                for update in data.get("result", []):
                                    self.offset = update["update_id"] + 1
                                    await self.process_update(update)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.error(f"Telegram Polling 發生錯誤: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(1)

    async def process_update(self, update):
        message = update.get("message")
        if not message:
            return

        chat_id = message["chat"]["id"]
        from_user_id = message["from"]["id"]
        text = message.get("text", "").strip()

        if not self.is_admin(from_user_id):
            await self.send_message(chat_id, "權限不足：您無權存取此機器人的管理系統。")
            return

        args = text.split()
        if not args:
            return

        command = args[0].lower()

        if command in ["/start", "/help"]:
            help_text = (
                "*Telegram 機器人控制面板指令*\n\n"
                "/status - 查看機器人硬體與系統狀態\n"
                "/servers - 查看機器人加入的所有伺服器清單\n"
                "/server <伺服器ID> - 查看特定伺服器資訊與成員列表\n"
                "/user <伺服器ID> <成員ID> - 查看成員詳細個人檔案\n"
            )
            await self.send_message(chat_id, help_text)

        elif command == "/status":
            await self.handle_status(chat_id)

        elif command in ["/servers", "/guilds"]:
            await self.handle_servers(chat_id)

        elif command == "/server":
            if len(args) < 2:
                await self.send_message(chat_id, "請提供伺服器 ID\n用法: `/server <伺服器ID>`")
            else:
                await self.handle_server_detail(chat_id, args[1])

        elif command == "/user":
            if len(args) < 3:
                await self.send_message(chat_id, "參數不足\n用法: `/user <伺服器ID> <成員ID>`")
            else:
                await self.handle_user_detail(chat_id, args[1], args[2])

    async def handle_status(self, chat_id):
        uptime = datetime.datetime.now() - self.start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}天 {hours}小時 {minutes}分 {seconds}秒"

        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        latency = round(self.bot.latency * 1000) if self.bot.latency else 0
        guild_count = len(self.bot.guilds)

        msg = (
            "*機器人狀態報告*\n\n"
            f"*機器人名稱*: {self.bot.user.name if self.bot.user else '未知'}\n"
            f"*連線狀態*: 在線\n"
            f"*伺服器總數*: {guild_count} 個\n"
            f"*Discord 延遲*: {latency} ms\n"
            f"*CPU 使用率*: {cpu} %\n"
            f"*RAM 使用率*: {ram} %\n"
            f"*已運行時間*: {uptime_str}\n"
        )
        await self.send_message(chat_id, msg)

    async def handle_servers(self, chat_id):
        guilds = self.bot.guilds
        if not guilds:
            await self.send_message(chat_id, "機器人目前未加入任何伺服器。")
            return

        msg = f"*伺服器列表 (共 {len(guilds)} 個)*\n\n"
        for idx, g in enumerate(guilds, 1):
            voice_status = "在語音中" if g.voice_client and g.voice_client.is_connected() else "未在語音"
            msg += f"{idx}. *{g.name}*\n"
            msg += f"   - ID: `{g.id}`\n"
            msg += f"   - 人數: {g.member_count} 人\n"
            msg += f"   - 語音狀態: {voice_status}\n\n"

        msg += "點擊指令查看詳情:\n`/server <伺服器ID>`"
        await self.send_message(chat_id, msg)

    async def handle_server_detail(self, chat_id, guild_id_str):
        try:
            guild_id = int(guild_id_str)
        except ValueError:
            await self.send_message(chat_id, "伺服器 ID 格式錯誤，必須為數字。")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            await self.send_message(chat_id, "找不到該伺服器，請確認 ID 是否正確。")
            return

        bots_count = len([m for m in guild.members if m.bot])
        humans_count = guild.member_count - bots_count

        msg = (
            f"*伺服器詳細資訊*: {guild.name}\n"
            f"*伺服器 ID*: `{guild.id}`\n"
            f"*擁有者 ID*: `{guild.owner_id}`\n"
            f"*總人數*: {guild.member_count} (成員: {humans_count} / 機器人: {bots_count})\n"
            f"*建立時間*: {guild.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"*成員清單 (前 25 名)*:\n"
        )

        for idx, member in enumerate(guild.members[:25], 1):
            bot_flag = " [BOT]" if member.bot else ""
            msg += f"{idx}. {member.display_name} (`{member.id}`){bot_flag}\n"

        if len(guild.members) > 25:
            msg += f"\n...還有 {len(guild.members) - 25} 位成員\n"

        msg += "\n查看特定成員個人檔案:\n`/user " + str(guild.id) + " <成員ID>`"
        await self.send_message(chat_id, msg)

    async def handle_user_detail(self, chat_id, guild_id_str, user_id_str):
        try:
            guild_id = int(guild_id_str)
            user_id = int(user_id_str)
        except ValueError:
            await self.send_message(chat_id, "伺服器 ID 或成員 ID 格式錯誤。")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            await self.send_message(chat_id, "找不到該伺服器。")
            return

        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                await self.send_message(chat_id, "在該伺服器中找不到指定成員。")
                return

        roles = [r.name for r in member.roles if r.name != "@everyone"]
        roles_str = ", ".join(roles) if roles else "無特別身分組"

        joined_at = member.joined_at.strftime('%Y-%m-%d %H:%M:%S') if member.joined_at else "未知"
        created_at = member.created_at.strftime('%Y-%m-%d %H:%M:%S')

        voice_state = "未在語音頻道"
        if member.voice and member.voice.channel:
            voice_state = f"在語音頻道 [{member.voice.channel.name}] 中"

        avatar_url = member.display_avatar.url if member.display_avatar else "無"

        msg = (
            f"*成員個人檔案資訊*\n\n"
            f"*顯示名稱*: {member.display_name}\n"
            f"*帳號名稱*: {member.name}\n"
            f"*用戶 ID*: `{member.id}`\n"
            f"*是否為機器人*: {'是' if member.bot else '否'}\n"
            f"*所在伺服器*: {guild.name}\n"
            f"*語音狀態*: {voice_state}\n"
            f"*帳號建立時間*: {created_at}\n"
            f"*加入伺服器時間*: {joined_at}\n"
            f"*身分組*: {roles_str}\n"
            f"*頭像連結*: [點此查看頭像]({avatar_url})\n"
        )
        await self.send_message(chat_id, msg)
