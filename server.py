# 修改 server.py 的 index 函式
@app.route('/', methods=['GET', 'POST'])
def index():
    admin_msg = ""
    if request.method == 'POST':
        # ... (密碼驗證邏輯保持不變)
        if request.form.get('password') == ADMIN_PASS:
            if request.form.get('action') == "clear_logs": bot_status["logs"] = []
            admin_msg = "操作已執行"
    
    # 產出伺服器狀態列表
    guild_rows = "".join([f"<tr><td>{g.name}</td><td>{g.member_count}</td><td>連線中</td></tr>" for g in _bot.guilds]) if _bot else "<tr><td colspan='3'>機器人尚未連線</td></tr>"
    guild_options = "".join([f'<option value="{g.id}">{g.name}</option>' for g in _bot.guilds]) if _bot else ""
    
    return f"""
    <html>
        <body style="background: #0f172a; color: white; padding: 20px; font-family: sans-serif;">
            <h1>機器人管理後台</h1>
            
            <div style="background: #1e293b; padding: 20px; border-radius: 8px;">
                <h3>系統狀態</h3>
                <p>CPU: {bot_status['cpu']}% | RAM: {bot_status['ram']}% | 延遲: {bot_status['latency']}ms</p>
                <table width="100%" border="1" style="border-collapse:collapse; margin-top:10px; color:white;">
                    <tr><th>伺服器名稱</th><th>成員數</th><th>狀態</th></tr>
                    {guild_rows}
                </table>
                <div id="logs" style="height: 100px; margin-top:10px; overflow-y: scroll; background: #000; padding: 10px; font-family: monospace;">{"<br>".join(bot_status['logs'])}</div>
            </div>
            
            <div style="margin-top: 20px; display: flex; gap: 20px;">
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>管理操作</h3>
                    <form method="POST">
                        <input type="password" name="password" placeholder="管理密碼" required><br>
                        <select name="action"><option value="clear_logs">清除日誌</option></select>
                        <button type="submit">執行</button>
                    </form>
                </div>
                <div style="background: #334155; padding: 20px; border-radius: 8px; flex: 1;">
                    <h3>廣播系統</h3>
                    <form action="/broadcast" method="POST">
                        <input type="password" name="password" placeholder="管理密碼" required><br>
                        <select id="guild_select" onchange="updateChannels()">
                            <option value="">選擇伺服器</option>{guild_options}
                        </select>
                        <select name="channel_id" id="channel_select">
                            <option value="">請先選擇伺服器</option>
                        </select><br>
                        <textarea name="message" placeholder="輸入訊息" required></textarea><br>
                        <button type="submit">發送廣播</button>
                    </form>
                </div>
            </div>
            <script>
            function updateChannels() {{
                let gid = document.getElementById('guild_select').value;
                fetch('/get_channels/' + gid).then(r => r.json()).then(data => {{
                    document.getElementById('channel_select').innerHTML = data.map(c => `<option value="${{c.id}}">${{c.name}}</option>`).join('');
                }});
            }}
            </script>
        </body>
    </html>
    """
