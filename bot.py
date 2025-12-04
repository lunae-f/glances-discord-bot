import discord
from discord import app_commands
import aiohttp
import asyncio
import os

# --- 設定部分 ---
TOKEN = os.getenv('DISCORD_TOKEN')
GLANCES_API_URL = os.getenv('GLANCES_API_URL', 'http://localhost:61208/api/4')

# 閾値設定
THRESHOLDS = {
    'cpu': {
        'usage_danger': 90,  # CPU使用率 警告(赤)
        'usage_warning': 75, # CPU使用率 注意(黄)
        'temp_danger': 100,   # CPU温度 警告(赤)
        'temp_warning': 80   # CPU温度 注意(黄)
    },
    'gpu': {
        'usage_danger': 101,  # GPU使用率 警告(赤)
        'usage_warning': 80, # GPU使用率 注意(黄)
        'temp_danger': 90,   # GPU温度 警告(赤)
        'temp_warning': 80   # GPU温度 注意(黄)
    },
    'memory': {
        'usage_danger': 90,  # メモリ使用率 警告(赤)
        'usage_warning': 75  # メモリ使用率 注意(黄)
    }
}
# ----------------

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    await tree.sync()
    print(f'Logged in as {client.user}')

async def fetch_glances_data(session, endpoint):
    url = f"{GLANCES_API_URL}/{endpoint}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                return await response.json()
            return None
    except Exception as e:
        print(f"Error fetching {endpoint}: {e}")
        return None

def get_status_emoji(value, danger_limit, warning_limit):
    """値としきい値を受け取って絵文字を返す"""
    if value is None: return "⚪" # データなし
    if value >= danger_limit: return "🔴"
    if value >= warning_limit: return "🟡"
    return "🟢"

def evaluate_health(cpu_usage, mem_usage, gpu_usage=None, cpu_temp=None, gpu_temp=None):
    """総合評価ロジック (個別しきい値対応)"""
    
    # 1. DANGER (警告) チェック
    d_reasons = []
    
    if cpu_usage >= THRESHOLDS['cpu']['usage_danger']: 
        d_reasons.append("CPU高負荷")
    if cpu_temp is not None and cpu_temp >= THRESHOLDS['cpu']['temp_danger']:
        d_reasons.append("CPU高温")
        
    if gpu_usage is not None and gpu_usage >= THRESHOLDS['gpu']['usage_danger']:
        d_reasons.append("GPU高負荷")
    if gpu_temp is not None and gpu_temp >= THRESHOLDS['gpu']['temp_danger']:
        d_reasons.append("GPU高温")
        
    if mem_usage >= THRESHOLDS['memory']['usage_danger']:
        d_reasons.append("メモリ不足")

    if d_reasons:
        return f"📛 **WARNING** ({', '.join(d_reasons)})", 0xff0000 # 赤色

    # 2. CAUTION (注意) チェック
    w_reasons = []
    
    if cpu_usage >= THRESHOLDS['cpu']['usage_warning']:
        w_reasons.append("CPU負荷気味")
    if cpu_temp is not None and cpu_temp >= THRESHOLDS['cpu']['temp_warning']:
        w_reasons.append("CPU温度上昇")
        
    if gpu_usage is not None and gpu_usage >= THRESHOLDS['gpu']['usage_warning']:
        w_reasons.append("GPU負荷気味")
    if gpu_temp is not None and gpu_temp >= THRESHOLDS['gpu']['temp_warning']:
        w_reasons.append("GPU温度上昇")
        
    if mem_usage >= THRESHOLDS['memory']['usage_warning']:
        w_reasons.append("メモリ多め")

    if w_reasons:
        return f"⚠️ **CAUTION** ({', '.join(w_reasons)})", 0xffff00 # 黄色

    # 3. 正常
    return "✅ **GOOD**", 0x00ff00 # 緑色

@tree.command(name="server_status", description="詳細なサーバー負荷状況を表示します")
async def server_status(interaction: discord.Interaction):
    await interaction.response.defer()

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            fetch_glances_data(session, 'cpu/total'),
            fetch_glances_data(session, 'mem'),
            fetch_glances_data(session, 'load'),
            fetch_glances_data(session, 'sensors'),
            fetch_glances_data(session, 'gpu'),
            return_exceptions=True
        )
        
        cpu_data = results[0] if isinstance(results[0], dict) else {'total': 0}
        mem_data = results[1] if isinstance(results[1], dict) else {'percent': 0, 'used': 0, 'total': 1}
        load_data = results[2] if isinstance(results[2], dict) else {'min1': 0, 'min5': 0, 'min15': 0}
        sensors_data = results[3] if isinstance(results[3], list) else []
        gpu_data_list = results[4] if isinstance(results[4], list) else []

    # --- データ抽出 ---
    cpu_usage = cpu_data.get('total', 0)

    # CPU温度
    cpu_temp_val = None
    cpu_temp_str = "N/A"
    for sensor in sensors_data:
        if 'Package id 0' in sensor.get('label', ''):
            cpu_temp_val = sensor.get('value')
            if cpu_temp_val is not None:
                cpu_temp_str = f"{cpu_temp_val}°C"
            break
    
    # GPU情報
    gpu_usage_val = None
    gpu_temp_val = None
    gpu_usage_str = "N/A"
    gpu_temp_str = "N/A"
    
    if gpu_data_list:
        gpu = gpu_data_list[0]
        gpu_usage_val = gpu.get('proc')
        gpu_temp_val = gpu.get('temperature')
        
        if gpu_usage_val is not None: gpu_usage_str = f"{gpu_usage_val}%"
        if gpu_temp_val is not None: gpu_temp_str = f"{gpu_temp_val}°C"

    # メモリ
    mem_usage = mem_data.get('percent', 0)
    mem_used_gb = round(mem_data.get('used', 0) / (1024**3), 2)
    mem_total_gb = round(mem_data.get('total', 1) / (1024**3), 2)

    load_avg = f"{load_data.get('min1')} / {load_data.get('min5')} / {load_data.get('min15')}"

    # 評価実行
    health_rank, color_code = evaluate_health(cpu_usage, mem_usage, gpu_usage_val, cpu_temp_val, gpu_temp_val)

    # --- Embed生成 ---
    embed = discord.Embed(title="📊 Server Status", color=color_code)
    embed.add_field(name="ステータス", value=health_rank, inline=False)
    
    # CPU
    cpu_emoji_usage = get_status_emoji(cpu_usage, THRESHOLDS['cpu']['usage_danger'], THRESHOLDS['cpu']['usage_warning'])
    cpu_emoji_temp = get_status_emoji(cpu_temp_val, THRESHOLDS['cpu']['temp_danger'], THRESHOLDS['cpu']['temp_warning'])
    embed.add_field(
        name="CPU", 
        value=f"使用率: {cpu_emoji_usage} **{cpu_usage}%**\n温度: {cpu_emoji_temp} **{cpu_temp_str}**", 
        inline=True
    )
    
    # GPU
    gpu_emoji_usage = get_status_emoji(gpu_usage_val, THRESHOLDS['gpu']['usage_danger'], THRESHOLDS['gpu']['usage_warning'])
    gpu_emoji_temp = get_status_emoji(gpu_temp_val, THRESHOLDS['gpu']['temp_danger'], THRESHOLDS['gpu']['temp_warning'])
    embed.add_field(
        name="GPU", 
        value=f"使用率: {gpu_emoji_usage} **{gpu_usage_str}**\n温度: {gpu_emoji_temp} **{gpu_temp_str}**", 
        inline=True
    )

    # Memory
    mem_emoji = get_status_emoji(mem_usage, THRESHOLDS['memory']['usage_danger'], THRESHOLDS['memory']['usage_warning'])
    embed.add_field(
        name="Memory", 
        value=f"使用率: {mem_emoji} **{mem_usage}%**\n({mem_used_gb}/{mem_total_gb} GB)", 
        inline=True
    )
    
    embed.add_field(name="Load Average", value=load_avg, inline=False)

    await interaction.followup.send(embed=embed)

client.run(TOKEN)