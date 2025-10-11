import discord
from discord.ext import commands
from discord import app_commands
import random
import math

class Sindan(commands.Cog):
    """診断系コマンドを提供"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="love-calculator",
        description="2人のユーザーの恋愛的な相性を診断します（ジョーク機能）"
    )
    @app_commands.describe(
        user1="1人目のユーザー",
        user2="2人目のユーザー"
    )
    async def love_calculator(
        self,
        interaction: discord.Interaction,
        user1: discord.User,
        user2: discord.User
    ) -> None:
        def complex_love_score(uid1: int, uid2: int) -> int:
            # 並び順を固定
            a, b = min(uid1, uid2), max(uid1, uid2)
            # 乱数シードを固定
            seed = (a * 987654321 + b * 123456789) ^ (a | b)
            random.seed(seed)
            # bit演算と三角関数
            base = ((a ^ b) & 0xFFFF) + ((a & b) % 97)
            trig = abs(math.sin(a % 360) * math.cos(b % 360))
            # 素数判定ボーナス
            def is_prime(n):
                if n < 2:
                    return False
                for i in range(2, int(n ** 0.5) + 1):
                    if n % i == 0:
                        return False
                return True
            prime_bonus = 7 if is_prime((a + b) % 100) else 0
            # 乱数要素
            rand = random.randint(0, 13)
            # 最終スコア
            score = int((base * trig * 1.7 + prime_bonus + rand) % 101)
            return max(0, min(score, 100))

        score = complex_love_score(user1.id, user2.id)

        # コメント生成（良いほど褒め、悪いほど辛辣に）
        if score == 100:
            comment = "💍 伝説級の運命！世界が祝福するレベル！"
        elif score >= 90:
            comment = "💖 まさに理想のカップル！映画化決定！"
        elif score >= 75:
            comment = "😍 かなり良い感じ！周囲も羨むベストマッチ！"
        elif score >= 60:
            comment = "😊 いい雰囲気！この先に期待大！"
        elif score >= 40:
            comment = "😐 普通…まあ悪くはない、かも？"
        elif score >= 20:
            comment = "🤨 うーん、ちょっと微妙…努力しないと厳しいかも"
        elif score > 0:
            comment = "😱 これは…正直おすすめできないレベル！"
        else:
            comment = "💔 伝説級の相性最悪！逆にネタにできるかも？"

        embed = discord.Embed(
            title="Love Calculator 💘",
            description=f"{user1.mention} × {user2.mention} の相性診断結果",
            color=discord.Color.pink()
        )
        embed.add_field(name="相性スコア", value=f"**{score} / 100**", inline=False)
        embed.add_field(name="コメント", value=comment, inline=False)
        embed.set_thumbnail(url=user1.display_avatar.url)
        embed.set_image(url=user2.display_avatar.url)
        embed.set_footer(text="※この診断はジョークです。真に受けないでね！")

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
	await bot.add_cog(Sindan(bot))
