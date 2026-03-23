import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Optional, Tuple
import logging
import asyncio
from .growth import GrowthPredictor

import discord
from discord import app_commands
from discord.ext import commands

import aiosqlite
import io
import matplotlib.pyplot as plt

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

ROLE_ID: Final[int] = int(os.getenv("ADMIN_ROLE_ID"))

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
        """メンバー参加時のイベントハンドラ"""
        if member.bot:
            return

        try:
            is_enabled, increment, channel_id = await WelcomeDatabase.get_settings(
                member.guild.id
            )
            if not is_enabled:
                return

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
                next_target = member_count + increment
                is_milestone = True
            else:
                next_target = member_count + (increment - remainder)
                is_milestone = False

            # 参加履歴取得
            join_dates = []
            async for m in member.guild.fetch_members(limit=None):
                if m.joined_at:
                    join_dates.append(m.joined_at)
            join_dates.sort()

            if is_milestone:
                # グラフ画像生成
                def create_growth_graph(join_dates, achieved_count):
                    if not join_dates:
                        return None
                    dates = [dt.date() for dt in join_dates]
                    unique_dates = sorted(set(dates))
                    counts = [sum(1 for d in dates if d <= ud) for ud in unique_dates]
                    fig, ax = plt.subplots(figsize=(6, 3))
                    ax.plot(unique_dates, counts, marker="o", color="#4e79a7")
                    ax.set_title("Member Growth History")
                    ax.set_xlabel("Date")
                    ax.set_ylabel("Members")
                    ax.grid(True, linestyle="--", alpha=0.5)
                    if achieved_count:
                        ax.annotate(
                            f"Milestone: {achieved_count} members!",
                            xy=(unique_dates[-1], counts[-1]),
                            xytext=(unique_dates[-1], counts[-1]+2),
                            color="crimson",
                            fontsize=12,
                            fontweight="bold",
                            arrowprops=dict(facecolor='crimson', shrink=0.05),
                            ha="right"
                        )
                    buf = io.BytesIO()
                    plt.tight_layout()
                    plt.savefig(buf, format="png", dpi=120)
                    plt.close(fig)
                    buf.seek(0)
                    return buf

                graph_buf = create_growth_graph(join_dates, member_count)

                # Embed作成（最初はNext milestone predictionフィールドなし）
                embed = discord.Embed(
                    title="🎉 Welcome EvexDevelopers! 🎉",
                    description=(
                        f"{member.mention} さん、ようこそ！\n"
                        f"現在のメンバー数: **{member_count}人**\n"
                        f"{member.guild.name}のメンバーが{member_count}人になりました！皆さんありがとうございます！\n"
                        f"良ければ、<#{os.getenv('INTRO_CHANNEL_ID')}>で自己紹介お願いします！"
                    ),
                    color=discord.Color.gold(),
                    timestamp=datetime.now()
                )
                embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
                embed.set_footer(text="EvexBot | Member Growth")

                file = None
                if graph_buf:
                    file = discord.File(graph_buf, filename="growth.png")
                    embed.set_image(url="attachment://growth.png")

                # まずEmbedのみ送信
                sent_msg = await channel.send(embed=embed, file=file)

                # 予測計算が終わったらembedをedit
                async def do_prediction_and_edit():
                    # GrowthPredictorで予測実行（Prophetモデル）
                    predictor = GrowthPredictor(join_dates, next_target, model_type="prophet")
                    prophet_model = await predictor.fit_prophet_model()
                    target_date = await predictor.predict(prophet_model)

                    # Embedを再構築してフィールド追加
                    new_embed = embed.copy()
                    if target_date:
                        days = (target_date.date() - datetime.now().date()).days
                        new_embed.add_field(
                            name="次の目標到達予測",
                            value=f"{next_target}人: {target_date.date()} (あと{days}日)",
                            inline=False
                        )
                    else:
                        new_embed.add_field(
                            name="次の目標到達予測",
                            value="予測できませんでした。",
                            inline=False
                        )
                    try:
                        await sent_msg.edit(embed=new_embed)
                    except Exception:
                        pass

                asyncio.create_task(do_prediction_and_edit())
            else:
                # Embedや画像なし、テキストメッセージのみ送信
                message = (
                    f"{member.mention} さん、ようこそ！\n"
                    f"現在のメンバー数: {member_count}人\n"
                    f"あと {increment - remainder} 人で {next_target}人達成です！\n"
                    f"良ければ、<#{os.getenv('INTRO_CHANNEL_ID')}>で自己紹介お願いします！"
                )
                sent_msg = await channel.send(message)

                # 予測計算が終わったらメッセージをeditしてNext milestone predictionを追記
                async def do_prediction_and_edit_text():
                    predictor = GrowthPredictor(join_dates, next_target, model_type="prophet")
                    prophet_model = await predictor.fit_prophet_model()
                    target_date = await predictor.predict(prophet_model)

                    if target_date:
                        days = (target_date.date() - datetime.now().date()).days
                        prediction_text = f"\n次の目標到達予測: {next_target}人: {target_date.date()} (あと{days}日)"
                    else:
                        prediction_text = "\n次の目標到達予測: 予測できませんでした。"
                    try:
                        await sent_msg.edit(content=message + prediction_text)
                    except Exception:
                        pass

                asyncio.create_task(do_prediction_and_edit_text())

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

    @commands.command(name="milestonetest")
    async def milestonetest(self, ctx: commands.Context):
        """マイルストーンお祝いEmbed＋グラフのテスト送信（管理者専用）"""
        # 実行者制限
        if ctx.author.id != int(os.getenv("ADMIN_USER_ID")):
            await ctx.send("権限がありません。")
            return

        member = ctx.author
        guild = ctx.guild
        if not guild:
            await ctx.send("サーバー内で実行してください。")
            return

        # 設定取得
        is_enabled, increment, channel_id = await WelcomeDatabase.get_settings(guild.id)
        member_count = len(guild.members)
        next_target = member_count + increment
        is_milestone = True  # 強制的にマイルストーン扱い

        # 参加履歴取得
        join_dates = []
        async for m in guild.fetch_members(limit=None):
            if m.joined_at:
                join_dates.append(m.joined_at)
        join_dates.sort()

        # グラフ画像生成
        def create_growth_graph(join_dates, achieved_count):
            if not join_dates:
                return None
            dates = [dt.date() for dt in join_dates]
            unique_dates = sorted(set(dates))
            counts = [sum(1 for d in dates if d <= ud) for ud in unique_dates]
            fig, ax = plt.subplots(figsize=(6, 3))
            ax.plot(unique_dates, counts, marker="o", color="#4e79a7")
            ax.set_title("Member Growth History")
            ax.set_xlabel("Date")
            ax.set_ylabel("Members")
            ax.grid(True, linestyle="--", alpha=0.5)
            if achieved_count:
                ax.annotate(
                    f"Milestone: {achieved_count} members!",
                    xy=(unique_dates[-1], counts[-1]),
                    xytext=(unique_dates[-1], counts[-1]+2),
                    color="crimson",
                    fontsize=12,
                    fontweight="bold",
                    arrowprops=dict(facecolor='crimson', shrink=0.05),
                    ha="right"
                )
            buf = io.BytesIO()
            plt.tight_layout()
            plt.savefig(buf, format="png", dpi=120)
            plt.close(fig)
            buf.seek(0)
            return buf

        graph_buf = create_growth_graph(join_dates, member_count)

        # Embed作成（最初はNext milestone predictionフィールドなし）
        embed = discord.Embed(
            title="🎉 Welcome EvexDevelopers! 🎉",
            description=(
                f"{member.mention} さん、ようこそ！\n"
                f"現在のメンバー数: **{member_count}人**\n"
                f"{guild.name}のメンバーが{member_count}人になりました！皆さんありがとうございます！"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_footer(text="EvexBot | Member Growth")

        file = None
        if graph_buf:
            file = discord.File(graph_buf, filename="growth.png")
            embed.set_image(url="attachment://growth.png")

        # まずEmbedのみ送信
        sent_msg = await ctx.send(embed=embed, file=file)

        # 予測計算が終わったらembedをedit
        async def do_prediction_and_edit():
            predictor = GrowthPredictor(join_dates, next_target, model_type="prophet")
            prophet_model = await predictor.fit_prophet_model()
            target_date = await predictor.predict(prophet_model)

            new_embed = embed.copy()
            if target_date:
                days = (target_date.date() - datetime.now().date()).days
                new_embed.add_field(
                    name="次の目標到達予測",
                    value=f"{next_target}人: {target_date.date()} (あと{days}日)",
                    inline=False
                )
            else:
                new_embed.add_field(
                    name="次の目標到達予測",
                    value="予測できませんでした。",
                    inline=False
                )
            try:
                await sent_msg.edit(embed=new_embed)
            except Exception:
                pass

        asyncio.create_task(do_prediction_and_edit())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberWelcomeCog(bot))
