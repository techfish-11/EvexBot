import io
from datetime import date, datetime, timedelta
from typing import List
import logging

import matplotlib.pyplot as plt
import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

class MembersHistory(commands.Cog):
    """指定された日付範囲のメンバー数推移をグラフ化して送信するCog"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _fetch_all_join_dates(self, guild: discord.Guild) -> List[datetime]:
        """サーバー内メンバーの joined_at を取得してソートしたリストを返す"""
        join_dates: List[datetime] = []
        async for m in guild.fetch_members(limit=None):
            if m.joined_at:
                join_dates.append(m.joined_at)
        join_dates.sort()
        return join_dates

    def _generate_counts(self, join_dates: List[datetime], start: date, end: date):
        """start から end までの日付ごとの累積メンバー数を生成する"""
        days = (end - start).days + 1
        dates = [start + timedelta(days=i) for i in range(days)]
        # join_dates may contain datetimes; compare by date()
        jd_dates = [d.date() for d in join_dates]
        counts = [sum(1 for jd in jd_dates if jd <= d) for d in dates]
        return dates, counts

    def _create_plot(self, dates: List[date], counts: List[int], start: date, end: date) -> io.BytesIO:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(dates, counts, marker="o", color="#2b8cbe")
        ax.set_title("Member Count History")
        ax.set_xlabel("Date")
        ax.set_ylabel("Members")
        ax.grid(True, linestyle="--", alpha=0.4)

        # Annotate start and end
        if counts:
            ax.annotate(f"{counts[0]}", xy=(dates[0], counts[0]), xytext=(0, 6), textcoords="offset points", ha="center")
            ax.annotate(f"{counts[-1]}", xy=(dates[-1], counts[-1]), xytext=(0, 6), textcoords="offset points", ha="center")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        buf.seek(0)
        return buf

    @discord.app_commands.command(
        name="members-history",
        description="指定した日付から指定した日付までのメンバー数推移をグラフ化します。"
    )
    @discord.app_commands.describe(
        start_date="開始日 (YYYY-MM-DD または YYYY/MM/DD)",
        end_date="終了日 (YYYY-MM-DD または YYYY/MM/DD)"
    )
    async def members_history(
        self,
        interaction: discord.Interaction,
        start_date: str,
        end_date: str,
    ) -> None:
        try:
            await interaction.response.defer(thinking=True)

            # 日付パース（YYYY-MM-DD または YYYY/MM/DD を許容）
            def _parse_date(s: object) -> date:
                if isinstance(s, date):
                    return s
                if isinstance(s, datetime):
                    return s.date()
                if not isinstance(s, str):
                    raise ValueError("日付は文字列で指定してください。形式: YYYY-MM-DD")
                for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        return datetime.strptime(s, fmt).date()
                    except Exception:
                        continue
                raise ValueError("日付は YYYY-MM-DD または YYYY/MM/DD の形式で指定してください。")

            try:
                start_date = _parse_date(start_date)
                end_date = _parse_date(end_date)
            except ValueError as ve:
                await interaction.followup.send(str(ve))
                return

            if start_date > end_date:
                await interaction.followup.send("開始日は終了日より前である必要があります。")
                return

            # 制限: 可視化範囲を過度に大きくしないため 3 年までに制限
            if (end_date - start_date).days > 365 * 3:
                await interaction.followup.send("日付の範囲は最大3年までにしてください。")
                return

            join_dates = await self._fetch_all_join_dates(interaction.guild)

            if not join_dates:
                await interaction.followup.send("参加履歴が見つかりません。メンバーの参加日時が取得できませんでした。")
                return

            dates, counts = self._generate_counts(join_dates, start_date, end_date)

            buf = self._create_plot(dates, counts, start_date, end_date)

            embed = discord.Embed(
                title="Member Count History",
                description=f"{start_date} から {end_date} までのメンバー数推移",
                color=discord.Color.blurple()
            )
            embed.add_field(name="開始時点のメンバー数", value=str(counts[0]), inline=True)
            embed.add_field(name="終了時点のメンバー数", value=str(counts[-1]), inline=True)
            embed.set_image(url="attachment://members_history.png")

            file = discord.File(buf, filename="members_history.png")
            await interaction.followup.send(embed=embed, file=file)

        except Exception as e:
            logger.error("Error in members-history command: %s", e, exc_info=True)
            await interaction.followup.send(f"エラーが発生しました: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MembersHistory(bot))
