import discord
from discord import app_commands
from discord.ext import tasks # 定期実行用に追加
import aiohttp
import asyncio
import os

# --- 設定部分 ---
TOKEN = os.getenv('DISCORD_TOKEN')
GLANCES_API_URL = os.getenv('GLANCES_API_URL', 'http://localhost:61208/api/4')
UPDATE_INTERVAL = 30 # ステータスを更新する間隔(秒)。これ以上短くするとAPI制限にかかる可能性があります

# 閾値設定
THRESHOLDS = {
    'cpu': {
        'usage_danger': 90,
        'usage_warning': 75,
        'temp_danger': 100,
        'temp_warning': 80
    },
    'gpu': {
        'usage_danger': 101,
        'usage_warning': 80,
        'temp_danger': 90,
        'temp_warning': 80
    },
    'memory': {
        'usage_danger': 90,
        'usage_warning': 75
    }
}
# ----------------

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

async def fetch_glances_data(session, endpoint):
    url = f"{GLANCES_API_URL}/{endpoint}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                return await response.json()
            return None
    except Exception:
        # 定期実行時はログがうるさくなるのでエラー表示を控えめにする
        return None

# --- 定期実行タスク: ステータス更新 ---
@tasks.loop(seconds=UPDATE_INTERVAL)
async def update_status_loop():
    async with aiohttp.ClientSession() as session:
        # 最低限必要なCPUとメモリだけ取得して軽量化
        results = await asyncio.gather(
            fetch_glances_data(session, 'cpu/total'),
            fetch_glances_data(session, 'mem'),
            fetch_glances_data(session, 'sensors'),
            return_exceptions=True
        )

        cpu_data = results[0] if isinstance(results[0], dict) else {'total': 0}
        mem_data = results[1] if isinstance(results[1], dict) else {'percent': 0}
        sensors_data = results[2] if isinstance(results[2], list) else []

        cpu_val = cpu_data.get('total', 0)
        mem_val = mem_data.get('percent', 0)
        
        # CPU温度取得 (Package id 0)
        temp_str = ""
        for sensor in sensors_data:
            if 'Package id 0' in sensor.get('label', ''):
                val = sensor.get('value')
                if val:
                    temp_str = f" | {val}°C"
                break

        # ステータス文言を作成 (例: "CPU: 12% | 45°C | Mem: 30%")
        status_text = f"CPU: {cpu_val}%{temp_str} | Mem: {mem_val}%"

        # 負荷状況に応じてステータスの種類を変える
        # 重い時は「Do Not Disturb (赤)」表示にするなどの演出
        status_type = discord.Status.online
        if cpu_val >= THRESHOLDS['cpu']['usage_danger'] or mem_val >= THRESHOLDS['memory']['usage_danger']:
            status_type = discord.Status.dnd # 取り込み中(赤アイコン)
        elif cpu_val >= THRESHOLDS['cpu']['usage_warning']:
            status_type = discord.Status.idle # 退席中(月アイコン)

        # Discordに反映
        await client.change_presence(
            status=status_type, 
            activity=discord.Activity(type=discord.ActivityType.watching, name=status_text)
        )

@client.event
async def on_ready():
    await tree.sync()
    # 定期実行タスクを開始
    if not update_status_loop.is_running():
        update_status_loop.start()
    print(f'Logged in as {client.user}')

# --- 既存のコマンド系処理 ---
def get_status_emoji(value, danger_limit, warning_limit):
    if value is None: return "⚪"
    if value >= danger_limit: return "🔴"
    if value >= warning_limit: return "🟡"
    return "🟢"

def format_alert_msg(alert):
    state = alert.get('state', 'UNKNOWN')
    atype = alert.get('type', 'General')
    return f"[{state}] {atype}"

def evaluate_health(cpu_usage, mem_usage, gpu_usage=None, cpu_temp=None, gpu_temp=None, alerts_data=None):
    # (前回と同じコードなので省略なしで記載します)
    glances_alert_level = 0
    alert_messages = []

    if alerts_data and isinstance(alerts_data, list):
        for alert in alerts_data:
            state = alert.get('state', '')
            msg = format_alert_msg(alert)
            if state == 'CRITICAL':
                glances_alert_level = max(glances_alert_level, 2)
                alert_messages.append(f"🔴 {msg}")
            elif state == 'WARNING':
                glances_alert_level = max(glances_alert_level, 1)
                alert_messages.append(f"🟡 {msg}")
            elif state == 'CAREFUL':
                glances_alert_level = max(glances_alert_level, 1)
                alert_messages.append(f"🟡 {msg}")

    d_reasons = []
    if glances_alert_level >= 2: d_reasons.append("Glances警告")
    if cpu_usage >= THRESHOLDS['cpu']['usage_danger']: d_reasons.append("CPU高負荷")
    if cpu_temp is not None and cpu_temp >= THRESHOLDS['cpu']['temp_danger']: d_reasons.append("CPU高温")
    if gpu_usage is not None and gpu_usage >= THRESHOLDS['gpu']['usage_danger']: d_reasons.append("GPU高負荷")
    if gpu_temp is not None and gpu_temp >= THRESHOLDS['gpu']['temp_danger']: d_reasons.append("GPU高温")
    if mem_usage >= THRESHOLDS['memory']['usage_danger']: d_reasons.append("メモリ不足")

    if d_reasons: return f"📛 **WARNING** ({', '.join(d_reasons)})", 0xff0000, alert_messages

    w_reasons = []
    if glances_alert_level >= 1: w_reasons.append("Glances注意")
    if cpu_usage >= THRESHOLDS['cpu']['usage_warning']: w_reasons.append("CPU負荷気味")
    if cpu_temp is not None and cpu_temp >= THRESHOLDS['cpu']['temp_warning']: w_reasons.append("CPU温度上昇")
    if gpu_usage is not None and gpu_usage >= THRESHOLDS['gpu']['usage_warning']: w_reasons.append("GPU負荷気味")
    if gpu_temp is not None and gpu_temp >= THRESHOLDS['gpu']['temp_warning']: w_reasons.append("GPU温度上昇")
    if mem_usage >= THRESHOLDS['memory']['usage_warning']: w_reasons.append("メモリ多め")

    if w_reasons: return f"⚠️ **CAUTION** ({', '.join(w_reasons)})", 0xffff00, alert_messages

    return "✅ **GOOD**", 0x00ff00, alert_messages

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
            fetch_glances_data(session, 'alert'),
            return_exceptions=True
        )
        
        cpu_data = results[0] if isinstance(results[0], dict) else {'total': 0}
        mem_data = results[1] if isinstance(results[1], dict) else {'percent': 0, 'used': 0, 'total': 1}
        load_data = results[2] if isinstance(results[2], dict) else {'min1': 0, 'min5': 0, 'min15': 0}
        sensors_data = results[3] if isinstance(results[3], list) else []
        gpu_data_list = results[4] if isinstance(results[4], list) else []
        alerts_data = results[5] if isinstance(results[5], list) else []

    cpu_usage = cpu_data.get('total', 0)
    cpu_temp_val = None
    cpu_temp_str = "N/A"
    for sensor in sensors_data:
        if 'Package id 0' in sensor.get('label', ''):
            cpu_temp_val = sensor.get('value')
            if cpu_temp_val is not None: cpu_temp_str = f"{cpu_temp_val}°C"
            break
    
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

    mem_usage = mem_data.get('percent', 0)
    mem_used_gb = round(mem_data.get('used', 0) / (1024**3), 2)
    mem_total_gb = round(mem_data.get('total', 1) / (1024**3), 2)
    load_avg = f"{load_data.get('min1')} / {load_data.get('min5')} / {load_data.get('min15')}"

    health_rank, color_code, alert_msgs = evaluate_health(
        cpu_usage, mem_usage, gpu_usage_val, cpu_temp_val, gpu_temp_val, alerts_data
    )

    embed = discord.Embed(title="📊 Server Status", color=color_code)
    embed.add_field(name="ステータス", value=health_rank, inline=False)
    
    cpu_emoji_usage = get_status_emoji(cpu_usage, THRESHOLDS['cpu']['usage_danger'], THRESHOLDS['cpu']['usage_warning'])
    cpu_emoji_temp = get_status_emoji(cpu_temp_val, THRESHOLDS['cpu']['temp_danger'], THRESHOLDS['cpu']['temp_warning'])
    embed.add_field(name="CPU", value=f"使用率: {cpu_emoji_usage} **{cpu_usage}%**\n温度: {cpu_emoji_temp} **{cpu_temp_str}**", inline=True)
    
    gpu_emoji_usage = get_status_emoji(gpu_usage_val, THRESHOLDS['gpu']['usage_danger'], THRESHOLDS['gpu']['usage_warning'])
    gpu_emoji_temp = get_status_emoji(gpu_temp_val, THRESHOLDS['gpu']['temp_danger'], THRESHOLDS['gpu']['temp_warning'])
    embed.add_field(name="GPU", value=f"使用率: {gpu_emoji_usage} **{gpu_usage_str}**\n温度: {gpu_emoji_temp} **{gpu_temp_str}**", inline=True)

    mem_emoji = get_status_emoji(mem_usage, THRESHOLDS['memory']['usage_danger'], THRESHOLDS['memory']['usage_warning'])
    embed.add_field(name="Memory", value=f"使用率: {mem_emoji} **{mem_usage}%**\n({mem_used_gb}/{mem_total_gb} GB)", inline=True)
    
    embed.add_field(name="Load Average", value=load_avg, inline=False)
    if alert_msgs:
        alert_text = "\n".join(alert_msgs[:5])
        embed.add_field(name="🚨 Active Alerts", value=alert_text, inline=False)

    await interaction.followup.send(embed=embed)

client.run(TOKEN)