import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import os
from datetime import datetime, timedelta
from typing import Optional
import asyncio


class MVP(commands.Cog):
    """
    MVP機能: メッセージ数とVC滞在時間を記録し、日次でランキングを発表する
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "mvp_data.db"
        self.target_guild_id = int(os.getenv("MVP_GUILD_ID", "0"))
        self.announcement_channel_id = int(os.getenv("MVP_ANNOUNCEMENT_CHANNEL_ID", "0"))
        
        # VCのミュート状態を追跡（user_id: {joined_at, unmuted_at, total_unmuted_time}）
        self.vc_sessions = {}
        
        # 初期化処理
        self.bot.loop.create_task(self.init_database())
        self.daily_announcement.start()
        self.cleanup_old_data.start()

    async def init_database(self):
        """データベースの初期化"""
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
            await db.commit()

    async def get_today_date(self) -> str:
        """今日の日付を取得（JST基準）"""
        return datetime.now().strftime("%Y-%m-%d")

    async def get_yesterday_date(self) -> str:
        """昨日の日付を取得（JST基準）"""
        yesterday = datetime.now() - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d")

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

    async def delete_old_data(self):
        """3日より古いデータを削除"""
        cutoff_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
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
        
        # Embedを作成
        embed = discord.Embed(
            title=f"🏆 {yesterday} のMVPランキング",
            description="お疲れ様でした！昨日の活動ランキングです",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        
        # ランキングを追加
        for i, (user_id, msg_count, vc_seconds, score) in enumerate(ranking, 1):
            user = await self.bot.fetch_user(user_id)
            username = user.name if user else f"User {user_id}"
            
            vc_minutes = vc_seconds // 60
            vc_hours = vc_minutes // 60
            vc_mins_remainder = vc_minutes % 60
            
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}位"
            
            value = (
                f"**スコア:** {score:.1f}点\n"
                f"📝 メッセージ: {msg_count}件\n"
                f"🎤 VC時間: {vc_hours}時間{vc_mins_remainder}分"
            )
            
            embed.add_field(
                name=f"{medal} {username}",
                value=value,
                inline=False
            )
        
        embed.set_footer(text="スコア = メッセージ数 + VC時間(分)")
        
        await channel.send(embed=embed)

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

    @app_commands.command(name="mvp", description="今日の暫定MVPランキングを表示します")
    async def mvp_command(self, interaction: discord.Interaction):
        """今日の暫定ランキングを表示"""
        today = await self.get_today_date()
        ranking = await self.get_ranking(today)
        
        if not ranking:
            await interaction.response.send_message(
                "まだ今日のデータがありません。",
                ephemeral=True
            )
            return
        
        # Embedを作成
        embed = discord.Embed(
            title=f"📊 {today} の暫定MVPランキング",
            description="現在のランキングです（リアルタイム更新）",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # ランキングを追加
        for i, (user_id, msg_count, vc_seconds, score) in enumerate(ranking, 1):
            user = await self.bot.fetch_user(user_id)
            username = user.name if user else f"User {user_id}"
            
            vc_minutes = vc_seconds // 60
            vc_hours = vc_minutes // 60
            vc_mins_remainder = vc_minutes % 60
            
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}位"
            
            value = (
                f"**スコア:** {score:.1f}点\n"
                f"📝 メッセージ: {msg_count}件\n"
                f"🎤 VC時間: {vc_hours}時間{vc_mins_remainder}分"
            )
            
            embed.add_field(
                name=f"{medal} {username}",
                value=value,
                inline=False
            )
        
        embed.set_footer(text="スコア = メッセージ数 + VC時間(分) | この日の集計は継続中です")
        
        await interaction.response.send_message(embed=embed)

    def cog_unload(self):
        """Cogがアンロードされるときの処理"""
        self.daily_announcement.cancel()
        self.cleanup_old_data.cancel()


async def setup(bot: commands.Bot):
    """Cogのセットアップ"""
    await bot.add_cog(MVP(bot))
