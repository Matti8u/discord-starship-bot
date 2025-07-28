import discord
from discord import app_commands
from discord.ext import tasks, commands
import requests
import time
import os
import json
from dotenv import load_dotenv
from keep_alive import keep_alive
from typing import cast
import functools

keep_alive()
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

if DISCORD_TOKEN is None:
    raise ValueError("DISCORD_TOKEN environment variable not set.")
if CLIENT_ID is None or CLIENT_SECRET is None:
    raise ValueError("CLIENT_ID or CLIENT_SECRET environment variable not set.")

CONFIG_PATH = "channel_config.json"

print("Bot script started", flush=True)

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)

channel_config = load_config()

# Your aircraft data mappings
last_seen_times = {
    "a671d3": 0,  # N514RS
    "ab42a6": 0,  # N8244L
    "ab1bdc": 0,  # N8149S
    "a5704b": 0,  # N45FL
    "a9b044": 0,  # N723SC
    "ac7be4": 0   # N903SC
}

icao_to_reg = {
    "a671d3": "N514RS",
    "ab42a6": "N8244L",
    "ab1bdc": "N8149S",
    "a5704b": "N45FL",
    "a9b044": "N723SC",
    "ac7be4": "N903SC"
}

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Decorator to safely handle interactions and suppress expired interaction errors
def safe_interaction_handler(func):
    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        try:
            await func(interaction, *args, **kwargs)
        except discord.NotFound:
            # Interaction expired or already responded to, safe to ignore
            print(f"⚠️ Interaction expired or not found for command '{interaction.command.name}'", flush=True)
        except Exception as e:
            print(f"❌ Unexpected error in command '{interaction.command.name}': {e}", flush=True)
    return wrapper

@tasks.loop(seconds=90)
async def check_aircraft_states():
    token_url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
    token_data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    token_headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    token_response = requests.post(token_url, data=token_data, headers=token_headers)
    if token_response.status_code != 200:
        print(f"Token request failed: {token_response.status_code} - {token_response.text}", flush=True)
        return

    token = token_response.json().get("access_token")
    api_url = "https://opensky-network.org/api/states/all"
    api_headers = {
        "Authorization": f"Bearer {token}"
    }
    api_params = {
        "icao24": ",".join(list(icao_to_reg.keys()))
    }
    api_response = requests.get(api_url, headers=api_headers, params=api_params)
    if api_response.status_code != 200:
        print(f"Open Sky API request failed: {api_response.status_code} - {api_response.text}", flush=True)
        return
    else:
        print("Open Sky API request successful", flush=True)
    data = api_response.json()
    if data.get("states") is None:
        return

    now = int(time.time())

    for item in data["states"]:
        icao24 = item[0]
        if icao24 in last_seen_times and now - last_seen_times[icao24] > 28800:
            reg = icao_to_reg[icao24]
            message = f"🚀 Starship {reg} has been spotted!\nhttps://globe.adsbexchange.com/?icao={icao24}"
            await send_alert_to_guilds(message)
            last_seen_times[icao24] = now

async def dm_owner_setup_message(guild: discord.Guild):
    try:
        # cast so Pyright knows this method exists
        owner = await bot.fetch_user(guild.owner_id)  # type: ignore
        await owner.send(
            f"👋 Hi! I noticed you haven't set a channel for aircraft alerts in **{guild.name}** yet.\n"
            "Please run the `/setchannel` command in the channel you want me to post alerts to."
        )
    except discord.Forbidden:
        print(f"Can't DM the owner of guild {guild.name} ({guild.id})", flush=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})", flush=True)
    try:
        await bot.tree.sync()
        print("Synced commands.", flush=True)

        for guild in bot.guilds:
            guild_id = str(guild.id)
            if guild_id not in channel_config:
                await dm_owner_setup_message(guild)

        if not check_aircraft_states.is_running():
            print("Starting check_aircraft_states loop...", flush=True)
            check_aircraft_states.start()
        else:
            print("Loop already running", flush=True)

    except Exception as e:
        print(f"Error in on_ready: {e}", flush=True)

@bot.event
async def on_guild_join(guild):
    guild_id = str(guild.id)
    if guild_id not in channel_config:
        await dm_owner_setup_message(guild)

@tree.command(name="setchannel", description="Set this channel for aircraft alerts")
@safe_interaction_handler
async def setchannel(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    if interaction.guild is None:
        await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    # user can be User or Member; ensure Member to access guild_permissions
    if not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("❌ Unable to verify permissions.", ephemeral=True)
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("❌ Only administrators can use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    channel_config[guild_id] = interaction.channel.id  # type: ignore
    save_config(channel_config)

    await interaction.followup.send("✅ This channel has been set for aircraft alerts.", ephemeral=True)

@tree.command(name="getchannel", description="Get the current aircraft alerts channel")
@safe_interaction_handler
async def getchannel(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    if interaction.guild is None:
        await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    channel_id = channel_config.get(guild_id)

    if channel_id:
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            await interaction.followup.send(f"📢 The current alerts channel is {channel.mention}.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ The saved channel no longer exists.", ephemeral=True)
    else:
        await interaction.followup.send("ℹ️ No alerts channel has been set yet. Use `/setchannel` to set one.", ephemeral=True)

async def send_alert_to_guilds(message: str):
    for guild in bot.guilds:
        guild_id = str(guild.id)
        channel_id = channel_config.get(guild_id)
        if not channel_id:
            continue
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) and channel.permissions_for(guild.me).send_messages:
            try:
                await channel.send(message)  # type: ignore
            except Exception as e:
                print(f"Failed to send message in {guild.name}: {e}", flush=True)


bot.run(DISCORD_TOKEN)
