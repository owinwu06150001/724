import os
import psutil
from flask import Flask, request, jsonify
from threading import Thread
from datetime import datetime

app = Flask(__name__)
_bot = None
broadcast_queue = [] 

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

ADMIN_PASS = os.environ.get("ADMIN_PASS", "111000")
bot_status = {"latency": 0, "cpu": 0, "ram": 0, "logs": [], "restart_requested": False}

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    bot_status["logs"].insert(0, f"[{timestamp}] {message}")
    if len(bot_status["logs"]) > 20: bot_status["logs"].pop()

@app.route('/status', methods=['GET'])
def get_status():
    if _bot and _bot.is_ready():
        bot_status["latency"] = round(_bot.latency * 1000)
        bot_status["cpu"] = psutil.cpu_percent()
        bot_status["ram"] = psutil.virtual_memory().percent
    return jsonify(bot_status)

@app.route('/get_channels/<int:guild_id>')
def get_channels(guild_id):
    if not _bot or not _bot.is_ready(): return jsonify([])
    guild = _bot.get_guild(guild_id)
    return jsonify([{"id": c.id, "name": c.name} for c in guild.text_channels]) if guild else jsonify([])

@app.route('/broadcast', methods=['POST'])
def broadcast():
    data = request.json
    if data.get('password') != ADMIN_PASS: return jsonify({"status": "error", "msg": "密碼錯誤"})
    broadcast_queue.append({"msg": data.get('message'), "cid": int(data.get('channel_id'))})
    return jsonify({"status": "success", "msg": "訊息已加入佇列"})

@app.route('/', methods=['GET', 'POST'])
def index():
    # 處理管理動作 (重啟/清空日誌)
    if request.method == 'POST':
        data = request.json
        if data.get('password') == ADMIN_PASS:
            if data.get('action') == "clear_logs": bot_status["logs"] = []
            elif data.get('action') == "restart": bot_status["restart_requested"] = True
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "msg": "密碼錯誤"})

    guild_list = _bot.guilds if _bot and _bot.is_ready() else []
    
    # 建立美化後的伺服器列表表格列
    guild_rows = "".join([
        f'<tr style="border-bottom: 1px solid #2d3748;">'
        f'<td style="padding: 12px; color: #e2e8f0;">{g.name}</td>'
        f'<td style="padding: 12px; color: #94a3b8;">{g.member_count}</td>'
        f'<td style="padding: 12px; color: #10b981; font-weight: 600;">連線中</td>'
        f'</tr>' for g in guild_list
    ])
    
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in guild_list])
    
    # 事先處理日誌換行，避免在 f-string 中引發引號解析衝突
    log_content = "<br>".join(bot_status['logs']) if bot_status['logs'] else "暫無日誌紀錄..."
    
    return f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>機器人管理後台</title>
        <style>
            body {{
                background: #0f172a;
                color: #f8fafc;
                padding: 30px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                margin: 0;
            }}
            .container {{
                max-width: 1100px;
                margin: 0 auto;
            }}
            h1 {{
                font-size: 28px;
                font-weight: 700;
                margin-bottom: 25px;
                color: #f1f5f9;
                border-left: 5px solid #5865F2;
                padding-left: 15px;
            }}
            h3 {{
                font-size: 18px;
                margin-top: 0;
                margin-bottom: 15px;
                color: #cbd5e1;
                font-weight: 600;
            }}
            .main-card {{
                background: #1e293b;
                padding: 24px;
                border-radius: 12px;
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
                border: 1px solid #334155;
                margin-bottom: 25px;
            }}
            .stats-bar {{
                display: flex;
                gap: 20px;
                background: #0f172a;
                padding: 12px 20px;
                border-radius: 8px;
                margin-bottom: 20px;
                border: 1px solid #1e293b;
                font-size: 14px;
                color: #94a3b8;
            }}
            .stats-bar span {{
                color: #38bdf8;
                font-weight: bold;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
                font-size: 15px;
            }}
            th {{
                background: #0f172a;
                color: #94a3b8;
                text-align: left;
                padding: 12px;
                font-weight: 600;
            }}
            .terminal {{
                height: 180px;
                overflow-y: auto;
                background: #090d16;
                color: #34d399;
                margin-top: 15px;
                padding: 15px;
                font-family: "Fira Code", Menlo, Monaco, Consolas, monospace;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #1e293b;
                line-height: 1.6;
            }}
            .grid-layout {{
                display: grid;
                grid-template-columns: 1fr 1.5fr;
                gap: 25px;
            }}
            .sub-card {{
                background: #1e293b;
                padding: 20px;
                border-radius: 12px;
                border: 1px solid #334155;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
            }}
            input, select, textarea {{
                width: 100%;
                background: #0f172a;
                border: 1px solid #475569;
                color: #f8fafc;
                padding: 10px 14px;
                border-radius: 6px;
                margin-bottom: 12px;
                font-size: 14px;
                box-sizing: border-box;
                transition: all 0.2s;
            }}
            input:focus, select:focus, textarea:focus {{
                outline: none;
                border-color: #5865F2;
                box-shadow: 0 0 0 3px rgba(88, 101, 242, 0.2);
            }}
            textarea {{
                resize: vertical;
                height: 90px;
            }}
            button {{
                background: #5865F2;
                color: white;
                border: none;
                padding: 10px 16px;
                border-radius: 6px;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.2s;
                font-size: 14px;
            }}
            button:hover {{
                background: #4752c4;
            }}
            .btn-danger {{
                background: #f43f5e;
            }}
            .btn-danger:hover {{
                background: #e11d48;
            }}
            .flex-buttons {{
                display: flex;
                gap: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>機器人管理後台</h1>
            
            <div class="main-card">
                <h3>系統監控</h3>
                <div class="stats-bar">
                     <div>CPU 使用率: <span id="s-cpu">{bot_status['cpu']}%</span></div>
                     <div>RAM 使用率: <span id="s-ram">{bot_status['ram']}%</span></div>
                     <div>網路延遲: <span id="s-lat">{bot_status['latency']}ms</span></div>
            </div>
                
                <table>
                    <thead>
                        <tr>
                            <th style="border-top-left-radius: 6px; border-bottom-left-radius: 6px;">伺服器名稱</th>
                            <th>成員數量</th>
                            <th style="border-top-right-radius: 6px; border-bottom-right-radius: 6px;">連線狀態</th>
                        </tr>
                    </thead>
                    <tbody>
                        {guild_rows if guild_rows else '<tr><td colspan="3" style="text-align:center; padding:20px; color:#64748b;">暫無伺服器連線資料</td></tr>'}
                    </tbody>
                </table>
                
                <div class="terminal" id="logs-container">
                    {log_content}
                </div>
            </div>
            
            <div class="grid-layout">
                <div class="sub-card">
                    <h3>系統管理</h3>
                    <input type="password" id="admin_pass" placeholder="請輸入管理員密碼">
                    <div class="flex-buttons">
                        <button onclick="act('clear_logs')" class="btn-danger" style="flex: 1;">清除日誌</button>
                        <button onclick="act('restart')" style="flex: 1; background: #64748b;">重啟機器人</button>
                    </div>
                </div>
                
                <div class="sub-card">
                    <h3>廣播系統</h3>
                    <div style="display: flex; gap: 10px;">
                        <select id="g_sel" onchange="upd()" style="flex: 1;">
                            <option value="">選擇目標伺服器</option>
                            {guild_options}
                        </select>
                        <select id="c_sel" style="flex: 1;">
                            <option value="">選擇文字頻道</option>
                        </select>
                    </div>
                    <textarea id="msg" placeholder="請輸入欲發送的訊息內容..."></textarea>
                    <button onclick="send()" style="width: 100%;">發送訊息到指定頻道</button>
                </div>
            </div>
        </div>

        <script>
            function upd() {{
                const guildId = document.getElementById('g_sel').value;
                const channelSelect = document.getElementById('c_sel');
                if (!guildId) {{
                    channelSelect.innerHTML = '<option value="">選擇文字頻道</option>';
                    return;
                }}
                fetch('/get_channels/' + guildId)
                .then(r => r.json())
                .then(d => {{
                    if (d.length === 0) {{
                        channelSelect.innerHTML = '<option value="">無可用文字頻道</option>';
                    }} else {{
                        channelSelect.innerHTML = d.map(c => `<option value="${{c.id}}"># ${{c.name}}</option>`).join('');
                    }}ss
                }});
                setInterval(() => {
    fetch('/status')
    .then(r => r.json())
    .then(d => {
        // 更新顯示數值
        document.getElementById('s-cpu').innerText = d.cpu + '%';
        document.getElementById('s-ram').innerText = d.ram + '%';
        document.getElementById('s-lat').innerText = d.latency + 'ms';
        
        // 更新日誌顯示
        const logsContainer = document.getElementById('logs-container');
        logsContainer.innerHTML = d.logs.join('<br>');
        logsContainer.scrollTop = logsContainer.scrollHeight;
    });
}, 3000);
            }}
            
            function send() {{
                const pwd = document.getElementById('admin_pass').value;
                const msgContent = document.getElementById('msg').value;
                const channelId = document.getElementById('c_sel').value;
                
                if (!pwd) {{ alert("請先輸入管理員密碼"); return; }}
                if (!channelId) {{ alert("請選擇目標頻道"); return; }}
                if (!msgContent.trim()) {{ alert("請輸入訊息內容"); return; }}
                
                fetch('/broadcast', {{
                    method: 'POST',
                    headers: {{"Content-Type": "application/json"}},
                    body: JSON.stringify({{
                        password: pwd,
                        message: msgContent,
                        channel_id: channelId
                    }})
                }})
                .then(r => r.json())
                .then(d => {{
                    alert(d.msg);
                    if (d.status === "success") {{
                        document.getElementById('msg').value = "";
                    }}
                }});
            }}
            
            function act(a) {{
                const pwd = document.getElementById('admin_pass').value;
                if (!pwd) {{ alert("請先輸入管理員密碼"); return; }}
                
                fetch('/', {{
                    method: 'POST',
                    headers: {{"Content-Type": "application/json"}},
                    body: JSON.stringify({{
                        password: pwd,
                        action: a
                    }})
                }})
                .then(r => r.json())
                .then(d => {{
                    if (d.status === 'success') {{
                        location.reload();
                    }} else {{
                        alert(d.msg);
                    }}
                }});
            }}

            const logBox = document.getElementById('logs-container');
            logBox.scrollTop = logBox.scrollHeight;
        </script>
    </body>
    </html>
    """

def keep_alive(): Thread(target=lambda: app.run(host='0.0.0.0', port=8080), daemon=True).start()
