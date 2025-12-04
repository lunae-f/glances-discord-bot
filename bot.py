import discord
from discord import app_commands
import aiohttp
import asyncio
import os

# --- 設定部分 ---
TOKEN = os.getenv('DISCORD_TOKEN')
GLANCES_API_URL = os.getenv('GLANCES_API_URL', 'http://localhost:61208/api/4')

# 閾値設定 (自由に変更してください)
THRESHOLDS = {
    'danger': { 'usage': 90, 'temp': 85 }, # これ以上で「警告」
    'warning': { 'usage': 75, 'temp': 75 } # これ以上で「注意」
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

def get_status_emoji(value, is_temp=False):
    """値に応じて絵文字を返す"""
    danger = THRESHOLDS['danger']['temp'] if is_temp else THRESHOLDS['danger']['usage']
    warning = THRESHOLDS['warning']['temp'] if is_temp else THRESHOLDS['warning']['usage']

    if value >= danger: return "🔴"
    if value >= warning: return "🟡"
    return "🟢"

def evaluate_health(cpu_usage, mem_usage, gpu_usage=None, cpu_temp=None, gpu_temp=None):
    """総合評価ロジック (OR条件)"""
    
    # 1. DANGER (警告) のチェック
    # どれか1つでも閾値(90%や85度)を超えていたら即アウト
    d_reasons = []
    t_danger = THRESHOLDS['danger']
    
    if cpu_usage >= t_danger['usage']: d_reasons.append("CPU高負荷")
    if mem_usage >= t_danger['usage']: d_reasons.append("メモリ不足")
    if gpu_usage is not None and gpu_usage >= t_danger['usage']: d_reasons.append("GPU高負荷")
    if cpu_temp is not None and isinstance(cpu_temp, (int, float)) and cpu_temp >= t_danger['temp']: d_reasons.append("CPU高温")
    if gpu_temp is not None and isinstance(gpu_temp, (int, float)) and gpu_temp >= t_danger['temp']: d_reasons.append("GPU高温")

    if d_reasons:
        return f"⚠️ **WARNING** ({', '.join(d_reasons)})", 0xff0000 # 赤色

    # 2. CAUTION (注意) のチェック
    # 警告ではないが、閾値(75%や75度)を超えているものがあるか
    w_reasons = []
    t_warning = THRESHOLDS['warning']

    if cpu_usage >= t_warning['usage']: w_reasons.append("CPU負荷気味")
    if mem_usage >= 80: w_reasons.append("メモリ多め") # メモリは80%を閾値に固定
    if gpu_usage is not None and gpu_usage >= t_warning['usage']: w_reasons.append("GPU負荷気味")
    if cpu_temp is not None and isinstance(cpu_temp, (int, float)) and cpu_temp >= t_warning['temp']: w_reasons.append("CPU温度上昇")
    if gpu_temp is not None and isinstance(gpu_temp, (int, float)) and gpu_temp >= t_warning['temp']: w_reasons.append("GPU温度上昇")

    if w_reasons:
        return f"🟡 **CAUTION** ({', '.join(w_reasons)})", 0xffff00 # 黄色

    # 3. 正常
    return "✅ **GOOD** (安定)", 0x00ff00 # 緑色

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

    # CPU温度 (Package id 0)
    cpu_temp_val = None
    cpu_temp_str = "N/A"
    for sensor in sensors_data:
        if 'Package id 0' in sensor.get('label', ''):
            cpu_temp_val = sensor.get('value')
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
    
    embed.add_field(
        name="CPU", 
        value=f"使用率: {get_status_emoji(cpu_usage)} **{cpu_usage}%**\n温度: {get_status_emoji(cpu_temp_val or 0, True)} **{cpu_temp_str}**", 
        inline=True
    )
    
    embed.add_field(
        name="GPU", 
        value=f"使用率: {get_status_emoji(gpu_usage_val or 0)} **{gpu_usage_str}**\n温度: {get_status_emoji(gpu_temp_val or 0, True)} **{gpu_temp_str}**", 
        inline=True
    )

    embed.add_field(
        name="Memory", 
        value=f"使用率: {get_status_emoji(mem_usage)} **{mem_usage}%**\n({mem_used_gb}/{mem_total_gb} GB)", 
        inline=True
    )
    
    embed.add_field(name="Load Average", value=load_avg, inline=False)

    await interaction.followup.send(embed=embed)

client.run(TOKEN)