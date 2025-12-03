import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

class Zikosyokai(commands.Cog):
	"""
	指定チャンネルに自己紹介テンプレを常に一番下に保つCog。
	チャンネルでメッセージが投稿・削除された際に、古いテンプレを削除して新しいテンプレを送信します。
	"""

	# 変更したいチャンネルIDをここにセット
	TARGET_CHANNEL_ID: int = 1445478071221223515
	# footerに付与するマーカー（検出用）
	MARKER: str = "evexbot_intro_v1"
	# チェックマーク絵文字
	CHECK_EMOJI: str = "✅"

	INTRO_TITLE = "自己紹介テンプレート"
	# コードブロックで送信するテンプレ（MARKER を含めて検出しやすくする）
	INTRO_CODEBLOCK: str = (
		"```text\n"
		"自己紹介テンプレート\n\n"
		"- 名前: \n"
		"- 得意分野: \n"
		"- ポートフォリオ: \n"
		"- 一言: \n\n"
		f"{MARKER}\n"
		"```"
	)

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._lock = asyncio.Lock()
		self._ready = False
		self.logger = logging.getLogger("Zikosyokai")

	def _is_intro_message(self, message: discord.Message) -> bool:
		"""メッセージが当Cogによるテンプレかどうか判定"""
		if message is None:
			return False
		if message.author != self.bot.user:
			return False
		# Embedのfooterで識別できるようにしている
		if message.embeds:
			embed = message.embeds[0]
			if embed.footer and embed.footer.text == self.MARKER:
				return True
		# フッタがない場合は本文のマーカーでも判定
		if isinstance(message.content, str) and self.MARKER in message.content:
			return True
		return False

	async def _find_and_delete_old_templates(self, channel: discord.TextChannel):
		"""指定チャンネル内の古いテンプレを削除"""
		async for m in channel.history(limit=200):
			if self._is_intro_message(m):
				try:
					await m.delete()
				except Exception as e:
					# 削除に失敗しても続行
					self.logger.exception("古いテンプレの削除に失敗しました: %s", e)

	def _build_intro_message(self) -> str:
		"""コードブロック形式のテンプレ本文を返す（contentで送信する）"""
		return self.INTRO_CODEBLOCK

	async def ensure_template_at_bottom(self, channel: discord.TextChannel):
		"""指定チャンネルにテンプレが一番下にあるか確認し、なければ再配置"""
		# ロックして多重実行を防ぐ
		async with self._lock:
			# 直近1件を取得
			last_msg: Optional[discord.Message] = None
			async for m in channel.history(limit=1):
				last_msg = m

			if last_msg and self._is_intro_message(last_msg):
				# 既に一番下にテンプレがある
				return

			# 古いテンプレを削除
			await self._find_and_delete_old_templates(channel)
			# 新しいテンプレを投稿（コードブロック形式を content にして送信）
			try:
				await channel.send(self._build_intro_message())
				self.logger.info("テンプレを再投稿しました。")
			except Exception as e:
				self.logger.exception("テンプレの投稿に失敗しました: %s", e)

	@commands.Cog.listener()
	async def on_ready(self):
		# Cogのon_readyは複数回呼ばれることがあるため最初のみ処理する
		if self._ready:
			return
		self._ready = True
		# 起動時にテンプレがあるか確認
		await self._try_ensure_channel()

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message):
		# 変更や自分が送ったテンプレに反応して再配置を繰り返さないようにする
		if message.channel.id != self.TARGET_CHANNEL_ID:
			return
		# 自分が送信したテンプレの場合は何もしない（ループ防止）
		if self._is_intro_message(message):
			return
		# それ以外のメッセージ（ユーザーや別ボット）が投稿されたので、テンプレを一番下にする
		await self.ensure_template_at_bottom(message.channel)

		# ユーザーの自己紹介メッセージにチェックマークを付ける（ボットの投稿はスキップ）
		if not message.author.bot:
			try:
				await message.add_reaction(self.CHECK_EMOJI)
			except Exception as e:
				# 権限がない等で失敗する可能性があるのでログに残す
				self.logger.exception("メッセージにチェックマークのリアクションを追加できませんでした: %s", e)

	@commands.Cog.listener()
	async def on_message_delete(self, message: discord.Message):
		# チャンネル内でテンプレが消えた場合などにテンプレが一番下にあるか確認して再投稿する
		if message.channel.id != self.TARGET_CHANNEL_ID:
			return
		await self.ensure_template_at_bottom(message.channel)

	async def _try_ensure_channel(self):
		"""チャンネルを取得してテンプレを確認する（on_ready時のヘルパー）"""
		try:
			channel = self.bot.get_channel(self.TARGET_CHANNEL_ID)
			if channel is None:
				# 取得できない場合はfetchする
				channel = await self.bot.fetch_channel(self.TARGET_CHANNEL_ID)
			if isinstance(channel, discord.TextChannel):
				await self.ensure_template_at_bottom(channel)
			else:
				self.logger.warning("対象のチャンネルが見つかりません: %s", self.TARGET_CHANNEL_ID)
		except Exception as e:
			self.logger.exception("チャンネル確認中にエラーが発生しました: %s", e)



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Zikosyokai(bot))