import os
import json
import logging
import asyncio
import websockets
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock
from flask_cors import CORS
import google.generativeai as genai
from google.cloud import storage
import datetime
from prompts import get_system_instruction, get_feedback_prompt

app = Flask(__name__)
CORS(app)
sock = Sock(app)
logging.basicConfig(level=logging.INFO)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "models/gemini-2.0-flash-exp"
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")



@app.route('/')
def index():
    return render_template('index.html')

# WebSocketプロキシ（変更なし）
@sock.route('/ws/realtime')
def realtime_proxy(ws_client):
    host = "generativelanguage.googleapis.com"
    url = f"wss://{host}/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"
    setup_msg = {
        "setup": {
            "model": MODEL_NAME,
            "system_instruction": { "parts": [{"text": get_system_instruction()}] },
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": { "voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}} }
            }
        }
    }
    # ... (プロキシ処理は前回と同じなので省略可、そのままでOK) ...
    async def proxy_handler():
        try:
            async with websockets.connect(url) as ws_gemini:
                await ws_gemini.send(json.dumps(setup_msg))
                async def forward_to_gemini():
                    while True:
                        try:
                            data = await asyncio.to_thread(ws_client.receive)
                            if data is None: break
                            await ws_gemini.send(data)
                        except: break
                async def forward_to_client():
                    async for msg in ws_gemini:
                        try:
                            if isinstance(msg, bytes): msg = msg.decode('utf-8')
                            ws_client.send(msg)
                        except: break
                await asyncio.gather(forward_to_gemini(), forward_to_client())
        except: pass
    try: asyncio.run(proxy_handler())
    except: pass


# ---------------------------------------------------------
# 📝 評価 (ファイルを受け取るように変更)
# ---------------------------------------------------------
@app.route('/feedback', methods=['POST'])
def feedback():
    audio_path = "temp_ai_response.wav"
    uploaded_audio = None
    response = None 
    
    try:
        # FormDataから取得
        conversation_log = request.form.get('log', '')
        audio_file = request.files.get('audio')

        # 音声を一時保存
        if audio_file:
            audio_file.save(audio_path)
            logging.info("Audio file received.")

        # タイムスタンプを生成
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        
        storage_client = None
        bucket = None
        
        # GCSクライアントとバケットを一度だけ初期化
        if GCS_BUCKET_NAME:
            try:
                storage_client = storage.Client()
                bucket = storage_client.bucket(GCS_BUCKET_NAME)
            except Exception as e:
                logging.error(f"Failed to initialize GCS Client: {e}. GCS uploads will be skipped.")
        
        # 1. 会話ログのGCSアップロード
        if conversation_log and bucket:
            try:
                log_filename = f"logs/log_users/log_{timestamp}.txt"
                
                # ★修正: UTF-8バイト列にし、charset=utf-8 を指定してアップロード
                blob = bucket.blob(log_filename)
                blob.upload_from_string(
                    conversation_log.encode('utf-8'), 
                    content_type='text/plain; charset=utf-8'
                )
                
                logging.info(f"Conversation log uploaded to gs://{GCS_BUCKET_NAME}/{log_filename}")
            except Exception as e:
                logging.error(f"Failed to upload log to GCS: {e}")


        # Gemini 1.5 Flash (マルチモーダル対応)
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-flash-latest')

        # 音声ファイルをアップロード
        if os.path.exists(audio_path):
           uploaded_audio = genai.upload_file(audio_path, mime_type="audio/wav")

        prompt = get_feedback_prompt(conversation_log)

        contents = [prompt]
        if uploaded_audio:
            contents.append(uploaded_audio)

        response = model.generate_content(contents)
        
        # 2. フィードバックの結果をGCSに保存
        if response and bucket: 
            try:
                feedback_filename = f"feedback/feedback_{timestamp}.md"
                
                # ★修正: UTF-8バイト列にし、charset=utf-8 を指定してアップロード
                blob = bucket.blob(feedback_filename)
                blob.upload_from_string(
                    response.text.encode('utf-8'), 
                    content_type='text/markdown; charset=utf-8'
                )
                
                logging.info(f"Feedback uploaded to gs://{GCS_BUCKET_NAME}/{feedback_filename}")
            except Exception as e:
                logging.error(f"Failed to upload feedback to GCS: {e}")

        # 後始末
        if uploaded_audio:
            try:
                genai.delete_file(uploaded_audio.name)
            except:
                pass
        if os.path.exists(audio_path):
            os.remove(audio_path)
            
        # responseが取得できていればそれを返す
        if response:
            return jsonify({"feedback": response.text})
        else:
             return jsonify({"error": "Gemini API failed to generate content."}), 500

    except Exception as e:
        logging.error(f"Feedback Error: {e}")
        if os.path.exists(audio_path):
            os.remove(audio_path)
        return jsonify({"error": str(e)}), 500#ihiarhuiauhriahufiaeuf
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)