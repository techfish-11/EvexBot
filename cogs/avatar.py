import discord
from discord.ext import commands
from typing import Final


NO_AVATAR_MESSAGE: Final[str] = "ユーザーはアイコンを設定していません。"
ERROR_MESSAGE: Final[str] = "アバターの取得中にエラーが発生しました: {}"
EMBED_COLOR: Final[int] = discord.Color.blue().value


class Avatar(commands.Cog):
    """ユーザーのアバター（アイコン）を表示する機能"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _create_avatar_embed(
        self,
        user: discord.User,
        avatar_url: str,
        is_default: bool = False,
        is_server: bool = False
    ) -> discord.Embed:
        if is_default:
            avatar_type = "デフォルトアイコン"
        elif is_server:
            avatar_type = "サーバーアイコン"
        else:
            avatar_type = "グローバルアイコン"
        embed = discord.Embed(
            title=f"{user.name}の{avatar_type}",
            color=EMBED_COLOR
        )
        embed.set_image(url=avatar_url)

        return embed

    @discord.app_commands.command(
        name="avatar",
        description="ユーザーのアイコンを表示します"
    )
    @discord.app_commands.describe(
        user="アバターを表示するユーザー",
        scope="グローバルかサーバーごとのアバターを選択"
    )
    @discord.app_commands.choices(scope=[
        discord.app_commands.Choice(name="サーバー", value="server"),
        discord.app_commands.Choice(name="グローバル", value="global"),
    ])
    async def avatar(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        scope: discord.app_commands.Choice[str] = None
    ) -> None:
        use_global = scope is not None and scope.value == "global"

        try:
            if not use_global and isinstance(user, discord.Member) and user.guild_avatar:
                embed = self._create_avatar_embed(
                    user=user,
                    avatar_url=user.guild_avatar.url,
                    is_server=True
                )
            elif user.avatar:
                embed = self._create_avatar_embed(
                    user=user,
                    avatar_url=user.avatar.url
                )
            elif user.default_avatar:
                embed = self._create_avatar_embed(
                    user=user,
                    avatar_url=user.default_avatar.url,
                    is_default=True
                )
            else:
                await interaction.response.send_message(
                    NO_AVATAR_MESSAGE,
                    ephemeral=True
                )
                return

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            await interaction.response.send_message(
                ERROR_MESSAGE.format(str(e)),
                ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Avatar(bot))