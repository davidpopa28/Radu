import asyncio
import concurrent

import discord
from discord.ext import commands
import yt_dlp as youtube_dl
from collections import deque

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)

song_queues = {}
player_tasks = {}

ytdlp_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': False,
    'default_search': 'auto',
    'quiet': True,
    'geo_bypass': True,
    'age_limit': 0,
    'extract_flat': True,
}

ytdlp_playlist_options = {
    'format': 'bestaudio/best',
    'noplaylist': False,
    'default_search': 'auto',
    'quiet': True,
    'geo_bypass': True,
    'age_limit': 0,
    'extract_flat': True,
}

ytdlp_video_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'auto',
    'quiet': True,
    'geo_bypass': True,
    'age_limit': 0,
    'extract_flat': False,
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -loglevel panic'
}

ytdlp = youtube_dl.YoutubeDL(ytdlp_format_options)
ytdlp_playlist = youtube_dl.YoutubeDL(ytdlp_playlist_options)
ytdlp_video = youtube_dl.YoutubeDL(ytdlp_video_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url):
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None, lambda: ytdlp_video.extract_info(url, download=False)
            )
            if data is None:
                raise Exception("Could not retrieve data from URL (may be age-restricted or unavailable).")
        except Exception as e:
            print(f"Error extracting info: {e}")
            return None

        if 'entries' in data:
            data = data['entries'][0]

        return cls(
            discord.FFmpegPCMAudio(
                data['url'],
                **ffmpeg_options
            ),
            data=data
        )


@bot.command(name='join', help='Join the voice channel you are in')
async def join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
        await ctx.send("Joined the voice channel!")
    else:
        await ctx.send("You are not connected to a voice channel.")


@bot.command(name='play', help='Add a song or playlist to the queue and play it')
async def play(ctx, *, url):
    async with ctx.typing():
        try:
            data = await bot.loop.run_in_executor(
                None, lambda: ytdlp_playlist.extract_info(url, download=False)
            )

            guild_id = ctx.guild.id

            if guild_id not in song_queues:
                song_queues[guild_id] = deque()

            added_songs = 0

            if 'entries' in data and data['entries']:
                entries = data['entries']

                tasks = []
                for entry in entries:
                    if entry is None:
                        continue

                    video_id = entry.get('id')
                    if video_id:
                        song_url = f"https://www.youtube.com/watch?v={video_id}"
                    else:
                        song_url = entry.get('url')

                    title = entry.get('title', 'Unknown Title')

                    tasks.append(process_song(song_url, guild_id, title, ctx))

                results = await asyncio.gather(*tasks)

                added_songs = sum(results)

            else:
                song_data = await bot.loop.run_in_executor(
                    None, lambda: ytdlp_video.extract_info(url, download=False)
                )
                song_queues[guild_id].append(song_data)
                added_songs += 1

            if added_songs == 0:
                await ctx.send("No suitable videos found (they may be age-restricted or unavailable).")
                return

            await ctx.send(f'Added {added_songs} song(s) to the queue.')

            if guild_id not in player_tasks or player_tasks[guild_id].done():
                player_tasks[guild_id] = bot.loop.create_task(player_loop(ctx))
        except Exception as e:
            await ctx.send(f'An error occurred: {str(e)}')

async def process_song(song_url, guild_id, title, ctx):
    try:
        ytdlp = youtube_dl.YoutubeDL(ytdlp_video_options)
        song_data = await bot.loop.run_in_executor(
            thread_pool, lambda: ytdlp.extract_info(song_url, download=False)
        )
        song_queues[guild_id].append(song_data)
        return 1
    except Exception as e:
        await ctx.send(f"Could not process '{title}': {e}")
        return 0

async def player_loop(ctx):
    guild_id = ctx.guild.id
    voice_client = ctx.voice_client

    while song_queues[guild_id]:
        try:
            song = song_queues[guild_id].popleft()
            url = song.get('webpage_url')
            title = song.get('title', 'Unknown Title')

            player = await YTDLSource.from_url(url)
            if player is None:
                await ctx.send(f"Could not play '{title}': age-restricted or unavailable.")
                continue

            voice_client.play(player, after=lambda e: print(f'Player error: {e}') if e else None)
            await ctx.send(f'Now playing: {title}')

            while voice_client.is_playing() or voice_client.is_paused():
                await asyncio.sleep(1)
        except Exception as e:
            print(f'Error in player loop: {e}')
            break

    await ctx.send("Queue is empty. Disconnecting from the voice channel.")
    await voice_client.disconnect()

    del song_queues[guild_id]
    player_tasks.pop(guild_id, None)


@bot.command(name='skip', help='Skip the current song')
async def skip(ctx):
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send("Skipped the song ⏭")
    else:
        await ctx.send("No song is currently playing.")


@bot.command(name='queue', help='Display the current song queue')
async def queue_command(ctx):
    guild_id = ctx.guild.id
    if guild_id in song_queues and song_queues[guild_id]:
        message = '**Song Queue:**\n'
        for idx, song in enumerate(song_queues[guild_id], start=1):
            title = song.get('title', 'Unknown Title')
            message += f'{idx}. {title}\n'
        await ctx.send(message)
    else:
        await ctx.send("The queue is empty.")


@bot.command(name='nowplaying', help='Show the currently playing song')
async def nowplaying(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        await ctx.send(f'Now playing: {ctx.voice_client.source.title}')
    else:
        await ctx.send("No song is currently playing.")


@bot.command(name='pause', help='Pause the currently playing song')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused ⏸")
    else:
        await ctx.send("No song is currently playing.")


@bot.command(name='resume', help='Resume the paused song')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed ⏯")
    else:
        await ctx.send("The song is not paused.")


@bot.command(name='stop', help='Stop playback and clear the queue')
async def stop(ctx):
    guild_id = ctx.guild.id
    if ctx.voice_client:
        ctx.voice_client.stop()
        song_queues[guild_id].clear()
        await ctx.send("Stopped ⏹ and cleared the queue.")
    else:
        await ctx.send("I'm not connected to a voice channel.")


@bot.command(name='leave', help='Disconnect the bot from the voice channel')
async def leave(ctx):
    guild_id = ctx.guild.id
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        song_queues.pop(guild_id, None)
        player_tasks.pop(guild_id, None)
        await ctx.send("Disconnected from the voice channel.")
    else:
        await ctx.send("I'm not connected to a voice channel.")


@play.before_invoke
async def ensure_voice(ctx):
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You need to be in a voice channel to play music.")
            raise commands.CommandError("Author not connected to a voice channel.")
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.voice_client.move_to(ctx.author.voice.channel)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Sorry, I didn't recognize that command. Type `!help` to see all commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: {error.param.name}. Type `!help {ctx.command}` for usage.")
    elif isinstance(error, commands.CommandInvokeError):
        await ctx.send(f"An error occurred while executing the command: {error.original}")
    else:
        await ctx.send("An unexpected error occurred.")
        print(f'Error: {error}')

bot.run('MTMxMDE1MzM1MjkxNDUzNDQ1MA.GEqjND.L-FceOI4rWJ7Ps3hAzghYjzjY9CsN-Dxx-vSyY')
