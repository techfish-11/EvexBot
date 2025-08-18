import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Optional, Tuple
import logging
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import aiosqlite

from .growth import GrowthPredictor

DB_PATH = Path("data/welcome.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

async def get_db_conn():
    return await aiosqlite.connect(DB_PATH)

DEFAULT_INCREMENT: Final[int] = 100
MIN_INCREMENT: Final[int] = 5
MAX_INCREMENT: Final[int] = 1000
JOIN_COOLDOWN: Final[int] = 3  # seconds

ERROR_MESSAGES: Final[dict] = {
    "no_permission": "コマンドを使用するにはサーバーの管理権限が必要です。",
    "invalid_action": "enableまたはdisableを指定してください。",
    "invalid_increment": f"{MIN_INCREMENT}～{MAX_INCREMENT}人の間で指定してください。",
    "no_channel": "ONにする場合はチャンネルを指定してください。"
}

SUCCESS_MESSAGES: Final[dict] = {
    "enabled": "参加メッセージをONにしました!\n{increment}人ごとに{channel}でお祝いメッセージを送信します",
    "disabled": "参加メッセージを無効にしました!"
}

WELCOME_MESSAGES: Final[dict] = {
    "milestone": (
        "🎉🎉🎉 お祝い 🎉🎉🎉\n"
        "{mention} さん、ようこそ！\n"
        "{member_count}人達成！\n"
        "{guild_name}のメンバーが{member_count}人になりました！皆さんありがとうございます！"
    ),
    "normal": (
        "{mention} さん、ようこそ！\n"
        "現在のメンバー数: {member_count}人\n"
        "あと {remaining} 人で {next_milestone}人達成です！"
    )
}

LEAVE_MESSAGES: Final[dict] = {
    "leave": (
        "{mention} さんがサーバーを退室しました。\n"
        "現在のメンバー数: {member_count}人"
    )
}

CREATE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS welcome_settings (
    guild_id INTEGER PRIMARY KEY,
    is_enabled INTEGER DEFAULT 0,
    member_increment INTEGER DEFAULT 100,
    channel_id INTEGER DEFAULT NULL
)
"""

CREATE_LEAVE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS leave_settings (
    guild_id INTEGER PRIMARY KEY,
    is_enabled INTEGER DEFAULT 0,
    channel_id INTEGER DEFAULT NULL
)
"""

logger = logging.getLogger(__name__)

class WelcomeDatabase:
    """ウェルカムメッセージの設定を管理するDB"""

    @staticmethod
    async def init_database() -> None:
        conn = await get_db_conn()
        await conn.execute(CREATE_TABLE_SQL)
        await conn.commit()
        await conn.close()

    @staticmethod
    async def get_settings(guild_id: int) -> Tuple[bool, int, Optional[int]]:
        conn = await get_db_conn()
        async with conn.execute(
            "SELECT is_enabled, member_increment, channel_id FROM welcome_settings WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            result = await cursor.fetchone()
        await conn.close()
        return (
            bool(result[0]),
            result[1],
            result[2]
        ) if result else (False, DEFAULT_INCREMENT, None)

    @staticmethod
    async def update_settings(guild_id: int, is_enabled: bool,
                              member_increment: Optional[int] = None,
                              channel_id: Optional[int] = None) -> None:
        conn = await get_db_conn()
        await conn.execute(
            """
            INSERT INTO welcome_settings (guild_id, is_enabled, member_increment, channel_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                is_enabled=excluded.is_enabled,
                member_increment=COALESCE(?, welcome_settings.member_increment),
                channel_id=COALESCE(?, welcome_settings.channel_id)
            """,
            (
                guild_id, int(is_enabled), member_increment, channel_id,
                member_increment, channel_id
            )
        )
        await conn.commit()
        await conn.close()

class LeaveDatabase:
    """退室メッセージの設定を管理するDB"""

    @staticmethod
    async def init_database() -> None:
        conn = await get_db_conn()
        await conn.execute(CREATE_LEAVE_TABLE_SQL)
        await conn.commit()
        await conn.close()

    @staticmethod
    async def get_settings(guild_id: int) -> Tuple[bool, Optional[int]]:
        conn = await get_db_conn()
        async with conn.execute(
            "SELECT is_enabled, channel_id FROM leave_settings WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            result = await cursor.fetchone()
        await conn.close()
        return (
            bool(result[0]),
            result[1]
        ) if result else (False, None)

    @staticmethod
    async def update_settings(guild_id: int, is_enabled: bool,
                              channel_id: Optional[int] = None) -> None:
        conn = await get_db_conn()
        await conn.execute(
            """
            INSERT INTO leave_settings (guild_id, is_enabled, channel_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                is_enabled=excluded.is_enabled,
                channel_id=COALESCE(?, leave_settings.channel_id)
            """,
            (
                guild_id, int(is_enabled), channel_id,
                channel_id
            )
        )
        await conn.commit()
        await conn.close()

ROLE_ID: Final[int] = 1255803402898898964

class MemberWelcomeCog(commands.Cog):
    """メンバー参加時のウェルカムメッセージを管理"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.last_welcome_time = {}

    async def cog_load(self) -> None:
        """Cogのロード時にDBを初期化"""
        await WelcomeDatabase.init_database()
        await LeaveDatabase.init_database()

    @app_commands.command(
        name="welcome",
        description="参加メッセージの設定"
    )
    @app_commands.describe(
        action="参加メッセージをON/OFFにします",
        increment="何人ごとにお祝いメッセージを送信するか設定 (デフォルト: 100)",
        channel="メッセージを送信するチャンネル"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="enable", value="enable"),
        app_commands.Choice(name="disable", value="disable")
    ])
    async def welcome_command(
        self,
        interaction: discord.Interaction,
        action: Literal["enable", "disable"],
        increment: Optional[int] = None,
        channel: Optional[discord.TextChannel] = None
    ) -> None:
        """ウェルカムメッセージの設定を行うコマンド"""
        # プライバシーモードのユーザーを無視
        # privacy_cog = self.bot.get_cog("Privacy")
        # if privacy_cog and privacy_cog.is_private_user(interaction.user.id):
        #     return

        # ロールIDチェック
        member = interaction.user
        if not any(role.id == ROLE_ID for role in getattr(member, "roles", [])):
            await interaction.response.send_message(
                ERROR_MESSAGES["no_permission"],
                ephemeral=True
            )
            return

        try:
            is_enabled = action == "enable"
            increment = increment or DEFAULT_INCREMENT

            if increment < MIN_INCREMENT or increment > MAX_INCREMENT:
                await interaction.response.send_message(
                    ERROR_MESSAGES["invalid_increment"],
                    ephemeral=True
                )
                return

            if is_enabled and not channel:
                await interaction.response.send_message(
                    ERROR_MESSAGES["no_channel"],
                    ephemeral=True
                )
                return

            channel_id = channel.id if channel else None
            await WelcomeDatabase.update_settings(
                interaction.guild_id,
                is_enabled,
                increment,
                channel_id
            )

            if is_enabled:
                await interaction.response.send_message(
                    SUCCESS_MESSAGES["enabled"].format(
                        increment=increment,
                        channel=channel.mention
                    ),
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    SUCCESS_MESSAGES["disabled"],
                    ephemeral=True
                )

        except Exception as e:
            logger.error("Error in welcome command: %s", e, exc_info=True)
            await interaction.response.send_message(
                f"エラーが発生しました: {e}",
                ephemeral=True
            )

    @app_commands.command(
        name="leave-message",
        description="退室メッセージの設定"
    )
    @app_commands.describe(
        action="退室メッセージをON/OFFにします",
        channel="メッセージを送信するチャンネル"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="enable", value="enable"),
        app_commands.Choice(name="disable", value="disable")
    ])
    async def leave_command(
        self,
        interaction: discord.Interaction,
        action: Literal["enable", "disable"],
        channel: Optional[discord.TextChannel] = None
    ) -> None:
        """退室メッセージの設定を行うコマンド"""
        # プライバシーモードのユーザーを無視
        # privacy_cog = self.bot.get_cog("Privacy")
        # if privacy_cog and privacy_cog.is_private_user(interaction.user.id):
        #     return

        # ロールIDチェック
        member = interaction.user
        if not any(role.id == ROLE_ID for role in getattr(member, "roles", [])):
            await interaction.response.send_message(
                ERROR_MESSAGES["no_permission"],
                ephemeral=True
            )
            return

        try:
            is_enabled = action == "enable"

            if is_enabled and not channel:
                await interaction.response.send_message(
                    ERROR_MESSAGES["no_channel"],
                    ephemeral=True
                )
                return

            channel_id = channel.id if channel else None
            await LeaveDatabase.update_settings(
                interaction.guild_id,
                is_enabled,
                channel_id
            )

            if is_enabled:
                await interaction.response.send_message(
                    f"退室メッセージをONにしました! チャンネル: {channel.mention}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "退室メッセージを無効にしました!",
                    ephemeral=True
                )

        except Exception as e:
            logger.error("Error in leave command: %s", e, exc_info=True)
            await interaction.response.send_message(
                f"エラーが発生しました: {e}",
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """メンバー参加時のイベントハンドラ（即時表示 + 後で予測を編集）"""
        if member.bot:
            return

        try:
            is_enabled, increment, channel_id = await WelcomeDatabase.get_settings(
                member.guild.id
            )
            if not is_enabled:
                return

            # 参加マクロ対策
            now = datetime.now()
            last_time = self.last_welcome_time.get(member.guild.id)
            if last_time and now - last_time < timedelta(seconds=JOIN_COOLDOWN):
                return
            self.last_welcome_time[member.guild.id] = now

            channel = member.guild.get_channel(channel_id)
            if not channel:
                await WelcomeDatabase.update_settings(
                    member.guild.id,
                    False
                )
                return

            member_count = len(member.guild.members)
            remainder = member_count % increment

            if remainder == 0:
                message_text = WELCOME_MESSAGES["milestone"].format(
                    mention=member.mention,
                    member_count=member_count,
                    guild_name=member.guild.name
                )
                next_milestone = member_count
            else:
                next_milestone = member_count + (increment - remainder)
                message_text = WELCOME_MESSAGES["normal"].format(
                    mention=member.mention,
                    member_count=member_count,
                    remaining=increment - remainder,
                    next_milestone=next_milestone
                )

            # 即時表示（予測は計算中と表示）
            message_text += "\n\n目標到達予想: 計算中...（後で更新されます）"
            sent_message = await channel.send(message_text)

            # バックグラウンドで予測を行い、結果でメッセージを編集する
            asyncio.create_task(
                self._compute_and_edit_prediction(member.guild, sent_message, next_milestone)
            )

        except Exception as e:
            logger.error(
                "Error processing member join: %s", e,
                exc_info=True
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """メンバー退室時のイベントハンドラ"""
        try:
            is_enabled, channel_id = await LeaveDatabase.get_settings(
                member.guild.id
            )
            if not is_enabled:
                return

            channel = member.guild.get_channel(channel_id)
            if not channel:
                await LeaveDatabase.update_settings(
                    member.guild.id,
                    False
                )
                return

            member_count = len(member.guild.members)
            message = LEAVE_MESSAGES["leave"].format(
                mention=member.mention,
                member_count=member_count
            )

            await channel.send(message)

        except Exception as e:
            logger.error(
                "Error processing member leave: %s", e,
                exc_info=True
            )

    async def _compute_and_edit_prediction(
        self,
        guild: discord.Guild,
        message: discord.Message,
        target: int
    ) -> None:
        """バックグラウンドで予測を行い、送信済メッセージを編集する"""
        try:
            # 参加日時データ収集（古い日付が先に来るようにソート）
            join_dates = []
            async for m in guild.fetch_members(limit=None):
                if m.joined_at:
                    join_dates.append(m.joined_at)
            join_dates.sort()

            if len(join_dates) < 2:
                try:
                    await message.edit(content=message.content + "\n予測: データが不足しているため計算できません。")
                except Exception:
                    logger.exception("Failed to edit message for insufficient data")
                return

            predictor = GrowthPredictor(join_dates, target, model_type="polynomial")

            # 予測（polynomial は内部で同期処理なので await 可能な形にしてある）
            predicted_date = await predictor.predict()

            if not predicted_date:
                try:
                    await message.edit(content=message.content + "\n予測: 予測範囲内で目標に到達しませんでした。")
                except Exception:
                    logger.exception("Failed to edit message when no target reach")
                return

            # モデルスコアと日数計算
            model_score = predictor.get_model_score()
            days_until = (predicted_date.date() - datetime.now().date()).days
            days_until_text = f"約{days_until}日後" if days_until >= 0 else f"{-days_until}日前（既に過ぎています）"

            pred_text = (
                f"\n予測到達日: {predicted_date.date()} ({days_until_text})\n"
                f"予測精度: {model_score:.2f}"
            )

            try:
                # 既存の「計算中」表示の下に追記する、安全のため append を使う
                await message.edit(content=message.content.replace("目標到達予想: 計算中...（後で更新されます）", "目標到達予想: " + pred_text))
            except Exception:
                # それでも失敗したら追記する
                try:
                    await message.edit(content=message.content + pred_text)
                except Exception:
                    logger.exception("Failed to edit message with prediction result")

        except Exception as e:
            logger.error("Error computing prediction: %s", e, exc_info=True)
            try:
                await message.edit(content=message.content + "\n予測: エラーが発生しました。")
            except Exception:
                logger.exception("Failed to edit message after exception")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberWelcomeCog(bot))