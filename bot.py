import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import yaml

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')

with open("config.yml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix=config["prefix"], intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

    tasks = []
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            tasks.append(bot.load_extension(f'cogs.{filename[:-3]}')) 

    await asyncio.gather(*tasks)

    await bot.tree.sync()

    print("All cogs loaded and commands synced!")

bot.run(TOKEN)