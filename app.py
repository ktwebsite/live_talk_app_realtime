import os
import json
import logging
import asyncio
import websockets
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock
from flask_cors import CORS
import google.generativeai as genai
from google.cloud import storage
import datetime
from dotenv import load_dotenv
from prompts import get_system_instruction, get_feedback_prompt

# 環境変数の読み込み
load_dotenv()

app = Flask(__name__)
CORS(app)
sock = Sock(app)
logging.basicConfig(level=logging.INFO)

# --- 1. 接続プールの事前初期化 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# モデルは gemini-2.0-flash-exp を使用
MODEL_NAME = "models/gemini-2.0-flash-exp"
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

# GCS Clientの初期化 (起動時に一度だけ)
storage_client = None
bucket = None
if GCS_BUCKET_NAME:
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        logging.info(f"GCS Bucket '{GCS_BUCKET_NAME}' initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize GCS Client: {e}")

# Gemini APIの初期化
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 非同期タスク用のスレッドプール
executor = ThreadPoolExecutor(max_workers=4)

@app.route('/')
def index():
    return render_template('index.html')

# --- 3. WebSocketプロキシの最適化 ---
@sock.route('/ws/realtime')
def realtime_proxy(ws_client):
    host = "generativelanguage.googleapis.com"
    url = f"wss://{host}/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"
    
    # プロンプトをprompts.pyから取得
    system_prompt_text = get_system_instruction()

    setup_msg = {
        "setup": {
            "model": MODEL_NAME,
            "system_instruction": { "parts": [{"text": system_prompt_text}] },
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": { "voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}} }
            }
        }
    }

    async def proxy_handler():
        try:
            # ping_intervalを設定して接続維持を強化
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws_gemini:
                # 初期設定を送信
                await ws_gemini.send(json.dumps(setup_msg))
                
                # 初回の挨拶を誘発するためのダミーメッセージを送るか、
                # クライアント側から最初の発話をするのを待つか。
                # ここでは接続確立のみ行い、クライアントからの音声/テキストを待つ。
                
                async def forward_to_gemini():
                    while True:
                        try:
                            # ブロッキングを防ぐため asyncio.to_thread を使用
                            data = await asyncio.to_thread(ws_client.receive)
                            if data is None: break
                            await ws_gemini.send(data)
                        except Exception as e:
                            logging.warning(f"Client receive error: {e}")
                            break
                            
                async def forward_to_client():
                    async for msg in ws_gemini:
                        try:
                            if isinstance(msg, bytes):
                                # バイナリデータ（音声）はそのまま転送
                                ws_client.send(msg)
                            else:
                                # テキストデータ（JSON）もそのまま転送
                                ws_client.send(msg)
                        except Exception as e:
                            logging.warning(f"Client send error: {e}")
                            break
                            
                await asyncio.gather(forward_to_gemini(), forward_to_client())
        except Exception as e:
            logging.error(f"WebSocket Proxy Error: {e}")
        finally:
            try: ws_client.close()
            except: pass

    try:
        asyncio.run(proxy_handler())
    except Exception as e:
        logging.error(f"Asyncio Run Error: {e}")


# --- 2. GCSアップロードの非同期化 ---
def upload_to_gcs_async(content, filename, content_type):
    """バックグラウンドでGCSにアップロードする関数"""
    if not bucket: return
    try:
        blob = bucket.blob(filename)
        blob.upload_from_string(
            content.encode('utf-8'), 
            content_type=content_type
        )
        logging.info(f"Async upload success: gs://{GCS_BUCKET_NAME}/{filename}")
    except Exception as e:
        logging.error(f"Async upload failed for {filename}: {e}")

@app.route('/feedback', methods=['POST'])
def feedback():
    audio_path = "temp_ai_response.wav"
    uploaded_audio = None
    
    try:
        conversation_log = request.form.get('log', '')
        audio_file = request.files.get('audio')

        if audio_file:
            audio_file.save(audio_path)
            logging.info("Audio file received.")

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # 1. 会話ログのアップロード (非同期)
        if conversation_log:
            log_filename = f"logs/log_users/log_{timestamp}.txt"
            executor.submit(upload_to_gcs_async, conversation_log, log_filename, 'text/plain; charset=utf-8')

        # Gemini 2.0 Flash Exp で評価生成
        # 評価用モデルは 2.0 Flash Exp を使用
        model = genai.GenerativeModel('models/gemini-2.0-flash-exp')

        if os.path.exists(audio_path):
           uploaded_audio = genai.upload_file(audio_path, mime_type="audio/wav")

        # プロンプトをprompts.pyから取得
        prompt = get_feedback_prompt(conversation_log)

        contents = [prompt]
        if uploaded_audio:
            contents.append(uploaded_audio)

        # ここは同期的に待つ必要がある（ユーザーに結果を返すため）
        response = model.generate_content(contents)
        
        # 2. フィードバック結果の保存 (非同期)
        if response:
            feedback_filename = f"feedback/feedback_{timestamp}.md"
            executor.submit(upload_to_gcs_async, response.text, feedback_filename, 'text/markdown; charset=utf-8')

        # 後始末
        if uploaded_audio:
            try: genai.delete_file(uploaded_audio.name)
            except: pass
        if os.path.exists(audio_path):
            os.remove(audio_path)
            
        if response:
            return jsonify({"feedback": response.text})
        else:
             return jsonify({"error": "Gemini API failed."}), 500

    except Exception as e:
        logging.error(f"Feedback Error: {e}")
        if os.path.exists(audio_path):
            os.remove(audio_path)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # 4. 本番環境対応 (threaded=True)
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
