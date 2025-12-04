import discord
from discord import app_commands
import aiohttp
import asyncio
import os # 追加

# --- 設定部分 (環境変数から読み込むように変更) ---
TOKEN = os.getenv('DISCORD_TOKEN')
GLANCES_API_URL = os.getenv('GLANCES_API_URL', 'http://localhost:61208/api/4')
# ----------------

# Intentsの設定
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    await tree.sync() # コマンドをDiscordに同期
    print(f'Logged in as {client.user}')

async def fetch_glances_data(session, endpoint):
    """Glances APIからデータを取得するヘルパー関数"""
    url = f"{GLANCES_API_URL}/{endpoint}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                return await response.json()
            return None
    except Exception as e:
        print(f"Error fetching {endpoint}: {e}")
        return None

@tree.command(name="server_status", description="サーバーの負荷状況を表示します")
async def server_status(interaction: discord.Interaction):
    # レスポンスが遅れる場合に備えて「考え中...」を表示
    await interaction.response.defer()

    async with aiohttp.ClientSession() as session:
        # 非同期で並列にデータを取得（高速化）
        cpu_task = fetch_glances_data(session, 'cpu/total')
        mem_task = fetch_glances_data(session, 'mem')
        load_task = fetch_glances_data(session, 'load')
        
        cpu_data, mem_data, load_data = await asyncio.gather(cpu_task, mem_task, load_task)

    # データが取れなかった場合のエラーハンドリング
    if not all([cpu_data, mem_data, load_data]):
        await interaction.followup.send("Glances APIからのデータ取得に失敗しました。")
        return

    # Embed（埋め込みメッセージ）の作成
    embed = discord.Embed(title="🖥️ Server Status", color=discord.Color.green())
    
    # CPU情報
    embed.add_field(
        name="CPU Usage", 
        value=f"{cpu_data['total']}%", 
        inline=True
    )
    
    # メモリ情報
    mem_percent = mem_data['percent']
    mem_used = round(mem_data['used'] / (1024**3), 2) # GB変換
    mem_total = round(mem_data['total'] / (1024**3), 2) # GB変換
    embed.add_field(
        name="Memory", 
        value=f"{mem_percent}% ({mem_used}GB / {mem_total}GB)", 
        inline=True
    )
    
    # ロードアベレージ
    embed.add_field(
        name="Load Average (1/5/15 min)", 
        value=f"{load_data['min1']} / {load_data['min5']} / {load_data['min15']}", 
        inline=False
    )

    await interaction.followup.send(embed=embed)

# Botの起動
client.run(TOKEN)