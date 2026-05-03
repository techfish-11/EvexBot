import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import os
from datetime import datetime, timedelta
from typing import Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

MVP_EMBED_COLOR = 0x2F3136
RANKING_DISPLAY_LIMIT = 5
DATA_RETENTION_DAYS = 30
DATABASE_SCHEMA_VERSION = 1
DISABLED_ENV_VALUES = {"0", "false", "off", "no"}


def _get_env_bool(name: str, default: bool = True) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized_value = raw_value.strip().lower()
    if normalized_value in DISABLED_ENV_VALUES:
        return False
    if normalized_value in {"1", "true", "on", "yes"}:
        return True

    logger.warning(
        "Invalid boolean value for %s=%r. Falling back to %s.",
        name,
        raw_value,
        default,
    )
    return default


class MVP(commands.Cog):
    """
    MVP機能: メッセージ数とVC滞在時間を記録し、日次でランキングを発表する
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "mvp_data.db"
        self.target_guild_id = int(os.getenv("MVP_GUILD_ID", "0"))
        self.announcement_channel_id = int(os.getenv("MVP_ANNOUNCEMENT_CHANNEL_ID", "0"))
        self.daily_announcement_enabled = _get_env_bool("MVP_DAILY_ANNOUNCEMENT_ENABLED", True)
        
        # VCのミュート状態を追跡（user_id: {joined_at, unmuted_at, total_unmuted_time}）
        self.vc_sessions = {}
        
        # 初期化処理
        self.bot.loop.create_task(self.init_database())
        if self.daily_announcement_enabled:
            self.daily_announcement.start()
        else:
            logger.info("MVP daily announcements are disabled by environment variable.")
        self.cleanup_old_data.start()

    async def init_database(self):
        """データベースの初期化とマイグレーション"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    vc_unmuted_seconds INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                )
            """)
            await self._run_migrations(db)
            await db.commit()

    async def _run_migrations(self, db: aiosqlite.Connection) -> None:
        """既存DBを安全に現在のスキーマへ移行する"""
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = int(row[0]) if row else 0

        if current_version > DATABASE_SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported MVP database schema version: {current_version}"
            )

        if current_version < 1:
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_stats_date_user
                ON daily_stats (date, user_id)
            """)
            await db.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")

    async def get_today_date(self) -> str:
        """今日の日付を取得（JST基準）"""
        return datetime.now().strftime("%Y-%m-%d")

    async def get_yesterday_date(self) -> str:
        """昨日の日付を取得（JST基準）"""
        yesterday = datetime.now() - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d")

    async def get_period_start_date(self, days: int) -> str:
        """指定日数分の開始日を取得（今日を含む）"""
        if not 1 <= days <= DATA_RETENTION_DAYS:
            raise ValueError(f"days must be between 1 and {DATA_RETENTION_DAYS}")
        start_date = datetime.now() - timedelta(days=days - 1)
        return start_date.strftime("%Y-%m-%d")

    async def increment_message_count(self, user_id: int, date: str):
        """メッセージ数を1増やす"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO daily_stats (user_id, date, message_count, vc_unmuted_seconds)
                VALUES (?, ?, 1, 0)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    message_count = message_count + 1
            """, (user_id, date))
            await db.commit()

    async def add_vc_time(self, user_id: int, date: str, seconds: int):
        """VC滞在時間（ミュート解除時間）を追加"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO daily_stats (user_id, date, message_count, vc_unmuted_seconds)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    vc_unmuted_seconds = vc_unmuted_seconds + ?
            """, (user_id, date, seconds, seconds))
            await db.commit()

    async def get_ranking(self, date: str) -> list[tuple]:
        """指定日のランキングを取得（メッセージ数 + VC時間でスコア計算）"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT user_id, message_count, vc_unmuted_seconds,
                       (message_count * 1.0 + vc_unmuted_seconds / 60.0) as score
                FROM daily_stats
                WHERE date = ?
                ORDER BY score DESC
                LIMIT 10
            """, (date,))
            return await cursor.fetchall()

    def _get_category_expression(self, category: str) -> str:
        """カテゴリに対応する安全なSQL式を返す"""
        category_expressions = {
            "text": "message_count",
            "voice": "CAST(vc_unmuted_seconds / 60 AS INTEGER)",
        }
        expression = category_expressions.get(category)
        if expression is None:
            raise ValueError(f"Unsupported MVP ranking category: {category}")
        return expression

    def _get_period_category_expression(self, category: str) -> str:
        """期間集計カテゴリに対応する安全なSQL集計式を返す"""
        category_expressions = {
            "text": "SUM(message_count)",
            "voice": "CAST(SUM(vc_unmuted_seconds) / 60 AS INTEGER)",
        }
        expression = category_expressions.get(category)
        if expression is None:
            raise ValueError(f"Unsupported MVP ranking category: {category}")
        return expression

    async def get_category_ranking(self, date: str, category: str, limit: Optional[int] = None) -> list[tuple[int, int]]:
        """指定日のカテゴリ別ランキングを取得する"""
        expression = self._get_category_expression(category)

        limit_clause = "" if limit is None else "LIMIT ?"
        params: tuple = (date,) if limit is None else (date, limit)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(f"""
                SELECT user_id, {expression} AS xp
                FROM daily_stats
                WHERE date = ? AND {expression} > 0
                ORDER BY xp DESC, user_id ASC
                {limit_clause}
            """, params)
            return await cursor.fetchall()

    async def get_category_ranking_for_period(
        self,
        start_date: str,
        end_date: str,
        category: str,
        limit: Optional[int] = None,
    ) -> list[tuple[int, int]]:
        """指定期間のカテゴリ別ランキングを取得する"""
        expression = self._get_period_category_expression(category)

        limit_clause = "" if limit is None else "LIMIT ?"
        params: tuple = (start_date, end_date) if limit is None else (start_date, end_date, limit)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(f"""
                SELECT user_id, {expression} AS xp
                FROM daily_stats
                WHERE date BETWEEN ? AND ?
                GROUP BY user_id
                HAVING xp > 0
                ORDER BY xp DESC, user_id ASC
                {limit_clause}
            """, params)
            return [(int(user_id), int(xp)) for user_id, xp in await cursor.fetchall()]

    async def get_user_category_rank(self, date: str, category: str, user_id: int) -> Optional[tuple[int, int]]:
        """指定ユーザーのカテゴリ別順位とXPを取得する"""
        expression = self._get_category_expression(category)

        async with aiosqlite.connect(self.db_path) as db:
            user_cursor = await db.execute(f"""
                SELECT {expression} AS xp
                FROM daily_stats
                WHERE date = ? AND user_id = ? AND {expression} > 0
            """, (date, user_id))
            user_row = await user_cursor.fetchone()
            if user_row is None:
                return None

            xp = int(user_row[0])
            rank_cursor = await db.execute(f"""
                SELECT COUNT(*) + 1
                FROM daily_stats
                WHERE date = ?
                  AND {expression} > 0
                  AND (
                    {expression} > ?
                    OR ({expression} = ? AND user_id < ?)
                  )
            """, (date, xp, xp, user_id))
            rank_row = await rank_cursor.fetchone()
            if rank_row is None:
                return None

            return int(rank_row[0]), xp

    async def get_user_category_rank_for_period(
        self,
        start_date: str,
        end_date: str,
        category: str,
        user_id: int,
    ) -> Optional[tuple[int, int]]:
        """指定期間における指定ユーザーのカテゴリ別順位とXPを取得する"""
        expression = self._get_period_category_expression(category)

        async with aiosqlite.connect(self.db_path) as db:
            user_cursor = await db.execute(f"""
                SELECT {expression} AS xp
                FROM daily_stats
                WHERE date BETWEEN ? AND ? AND user_id = ?
                GROUP BY user_id
                HAVING xp > 0
            """, (start_date, end_date, user_id))
            user_row = await user_cursor.fetchone()
            if user_row is None:
                return None

            xp = int(user_row[0])
            rank_cursor = await db.execute(f"""
                SELECT COUNT(*) + 1
                FROM (
                    SELECT user_id, {expression} AS xp
                    FROM daily_stats
                    WHERE date BETWEEN ? AND ?
                    GROUP BY user_id
                    HAVING xp > 0
                       AND (
                         xp > ?
                         OR (xp = ? AND user_id < ?)
                       )
                )
            """, (start_date, end_date, xp, xp, user_id))
            rank_row = await rank_cursor.fetchone()
            if rank_row is None:
                return None

            return int(rank_row[0]), xp

    def _format_leaderboard_entry(self, rank: int, user_id: int, xp: int) -> str:
        """Embed用のランキング行を作成する"""
        return f"#{rank} | <@{user_id}> XP: {xp}"

    async def _build_leaderboard_lines(
        self,
        date: str,
        category: str,
        viewer_id: Optional[int] = None,
    ) -> list[str]:
        """トップランキングと必要に応じた閲覧者の順位行を作成する"""
        top_ranking = await self.get_category_ranking(date, category, RANKING_DISPLAY_LIMIT)
        lines = [
            self._format_leaderboard_entry(rank, user_id, xp)
            for rank, (user_id, xp) in enumerate(top_ranking, 1)
        ]

        if viewer_id is not None:
            viewer_rank = await self.get_user_category_rank(date, category, viewer_id)
            if viewer_rank is not None and all(user_id != viewer_id for user_id, _ in top_ranking):
                rank, xp = viewer_rank
                lines.append(self._format_leaderboard_entry(rank, viewer_id, xp))

        return lines or ["データなし"]

    async def _build_period_leaderboard_lines(
        self,
        start_date: str,
        end_date: str,
        category: str,
        viewer_id: Optional[int] = None,
    ) -> list[str]:
        """期間指定のトップランキングと閲覧者の順位行を作成する"""
        top_ranking = await self.get_category_ranking_for_period(
            start_date,
            end_date,
            category,
            RANKING_DISPLAY_LIMIT,
        )
        lines = [
            self._format_leaderboard_entry(rank, user_id, xp)
            for rank, (user_id, xp) in enumerate(top_ranking, 1)
        ]

        if viewer_id is not None:
            viewer_rank = await self.get_user_category_rank_for_period(
                start_date,
                end_date,
                category,
                viewer_id,
            )
            if viewer_rank is not None and all(user_id != viewer_id for user_id, _ in top_ranking):
                rank, xp = viewer_rank
                lines.append(self._format_leaderboard_entry(rank, viewer_id, xp))

        return lines or ["データなし"]

    async def create_leaderboard_embed(
        self,
        date: str,
        *,
        viewer_id: Optional[int] = None,
    ) -> discord.Embed:
        """2カラム形式のMVP Embedを作成する"""
        text_lines, voice_lines = await asyncio.gather(
            self._build_leaderboard_lines(date, "text", viewer_id),
            self._build_leaderboard_lines(date, "voice", viewer_id),
        )

        title = "Evexスコアランキング)"

        embed = discord.Embed(
            title=title,
            color=MVP_EMBED_COLOR,
            timestamp=datetime.now(),
        )
        embed.add_field(name="テキスト 💬", value="\n".join(text_lines), inline=True)
        embed.add_field(name="通話 🎙️", value="\n".join(voice_lines), inline=True)
        embed.set_footer(text=f"集計日: {date} | テキスト XP = メッセージ数 / 通話 XP = VC時間(分)")
        return embed

    async def create_period_leaderboard_embed(
        self,
        start_date: str,
        end_date: str,
        days: int,
        *,
        viewer_id: Optional[int] = None,
    ) -> discord.Embed:
        """指定期間のMVP Embedを作成する"""
        text_lines, voice_lines = await asyncio.gather(
            self._build_period_leaderboard_lines(start_date, end_date, "text", viewer_id),
            self._build_period_leaderboard_lines(start_date, end_date, "voice", viewer_id),
        )

        title = f"Evexスコアランキング (過去 {days}d)"
        embed = discord.Embed(
            title=title,
            color=MVP_EMBED_COLOR,
            timestamp=datetime.now(),
        )
        embed.add_field(name="テキスト 💬", value="\n".join(text_lines), inline=True)
        embed.add_field(name="通話 🎙️", value="\n".join(voice_lines), inline=True)
        embed.set_footer(
            text=f"集計期間: {start_date} - {end_date} | テキスト XP = メッセージ数 / 通話 XP = VC時間(分)"
        )
        return embed

    def create_daily_announcement_embed(self, date: str, ranking: list[tuple]) -> discord.Embed:
        """日次アナウンス専用の総合ランキングEmbedを作成する"""
        embed = discord.Embed(
            title=f"{date} のMVPランキング",
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )

        for rank, (user_id, message_count, vc_seconds, score) in enumerate(ranking[:10], 1):
            vc_minutes = int(vc_seconds) // 60
            vc_hours, vc_mins_remainder = divmod(vc_minutes, 60)
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"{rank}位"

            embed.add_field(
                name=f"{medal} <@{user_id}>",
                value=(
                    f"スコア: {float(score):.1f}点\n"
                    f"メッセージ: {int(message_count)}件\n"
                    f"VC時間: {vc_hours}時間{vc_mins_remainder}分"
                ),
                inline=False,
            )

        embed.set_footer(text="スコア = メッセージ数 + VC時間(分)")
        return embed

    async def delete_old_data(self):
        """保持期間より古いデータを削除"""
        cutoff_date = (datetime.now() - timedelta(days=DATA_RETENTION_DAYS - 1)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM daily_stats WHERE date < ?", (cutoff_date,))
            await db.commit()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """メッセージが送信されたときの処理"""
        # Botのメッセージは無視
        if message.author.bot:
            return
        
        # 対象サーバーのメッセージのみカウント
        if message.guild and message.guild.id == self.target_guild_id:
            today = await self.get_today_date()
            await self.increment_message_count(message.author.id, today)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, 
        member: discord.Member, 
        before: discord.VoiceState, 
        after: discord.VoiceState
    ):
        """VC状態が変更されたときの処理"""
        # 対象サーバーのみ処理
        if member.guild.id != self.target_guild_id:
            return
        
        # Botは無視
        if member.bot:
            return

        user_id = member.id
        now = datetime.now()
        today = await self.get_today_date()

        # VCに参加した場合
        if before.channel is None and after.channel is not None:
            self.vc_sessions[user_id] = {
                "joined_at": now,
                "unmuted_at": now if not after.self_mute else None,
                "total_unmuted_seconds": 0
            }
        
        # VCから退出した場合
        elif before.channel is not None and after.channel is None:
            if user_id in self.vc_sessions:
                session = self.vc_sessions[user_id]
                
                # ミュート解除中だった場合、その時間を記録
                if session["unmuted_at"] is not None:
                    unmuted_duration = (now - session["unmuted_at"]).total_seconds()
                    session["total_unmuted_seconds"] += unmuted_duration
                
                # 合計ミュート解除時間をDBに保存
                total_seconds = int(session["total_unmuted_seconds"])
                if total_seconds > 0:
                    await self.add_vc_time(user_id, today, total_seconds)
                
                # セッション情報を削除
                del self.vc_sessions[user_id]
        
        # ミュート状態が変更された場合
        elif before.channel is not None and after.channel is not None:
            if user_id in self.vc_sessions:
                session = self.vc_sessions[user_id]
                
                # ミュート解除された場合
                if before.self_mute and not after.self_mute:
                    session["unmuted_at"] = now
                
                # ミュートされた場合
                elif not before.self_mute and after.self_mute:
                    if session["unmuted_at"] is not None:
                        unmuted_duration = (now - session["unmuted_at"]).total_seconds()
                        session["total_unmuted_seconds"] += unmuted_duration
                        session["unmuted_at"] = None

    @tasks.loop(hours=24)
    async def daily_announcement(self):
        """毎日0時に前日のランキングを発表"""
        if not self.daily_announcement_enabled:
            return

        now = datetime.now()
        
        # 0時まで待機
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        # 前日のランキングを取得
        yesterday = await self.get_yesterday_date()
        ranking = await self.get_ranking(yesterday)
        
        if not ranking:
            return
        
        # アナウンスチャンネルを取得
        channel = self.bot.get_channel(self.announcement_channel_id)
        if not channel:
            return
        
        embed = self.create_daily_announcement_embed(yesterday, ranking)
        
        await channel.send(
            content="お疲れ様でした！昨日の活動ランキングです！",
            embed=embed,
        )

    @daily_announcement.before_loop
    async def before_daily_announcement(self):
        """タスク開始前にBotの準備完了を待つ"""
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def cleanup_old_data(self):
        """24時間ごとに古いデータを削除"""
        await self.delete_old_data()

    @cleanup_old_data.before_loop
    async def before_cleanup_old_data(self):
        """タスク開始前にBotの準備完了を待つ"""
        await self.bot.wait_until_ready()

    @app_commands.command(name="mvp", description="指定期間のMVPランキングを表示します")
    @app_commands.describe(days="集計期間（日数）。1から30まで指定できます。")
    async def mvp_command(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, DATA_RETENTION_DAYS] = 1,
    ):
        """指定期間の暫定ランキングを表示"""
        # Show the thinking indicator to the user while preparing the response
        await interaction.response.defer(thinking=True)
        try:
            today = await self.get_today_date()
            start_date = await self.get_period_start_date(days)
            ranking = await self.get_category_ranking_for_period(start_date, today, "text", 1)
            voice_ranking = await self.get_category_ranking_for_period(start_date, today, "voice", 1)
            
            if not ranking and not voice_ranking:
                await interaction.followup.send(
                    "指定期間のデータがありません。",
                    ephemeral=True
                )
                return
            
            embed = await self.create_period_leaderboard_embed(
                start_date,
                today,
                days,
                viewer_id=interaction.user.id,
            )
            
            await interaction.followup.send(embed=embed)
        except Exception:
            logger.exception("Exception in mvp_command")
            # Attempt to inform the user; fallback to logging if it fails
            try:
                await interaction.followup.send("エラーが発生しました。後で再試行してください。", ephemeral=True)
            except Exception:
                logger.exception("Failed to send error followup in mvp_command")

    def cog_unload(self):
        """Cogがアンロードされるときの処理"""
        if self.daily_announcement.is_running():
            self.daily_announcement.cancel()
        self.cleanup_old_data.cancel()


async def setup(bot: commands.Bot):
    """Cogのセットアップ"""
    await bot.add_cog(MVP(bot))
