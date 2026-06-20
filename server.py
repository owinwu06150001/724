from flask import Flask, jsonify
from flask_cors import CORS
from threading import Thread
import os

app = Flask(__name__)
CORS(app)

# 這是 main.py 會持續更新的字典
bot_status = {"guild_count": 0, "user_count": 0, "latency": 0}

@app.route('/api/data', methods=['GET'])
def get_data():
    return jsonify(bot_status)

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
