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
            "system_instruction": { "parts": [{"text": "あなたはIT企業の導入担当者（顧客）です...（略）"}] },
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
    try:
        # FormDataから取得
        conversation_log = request.form.get('log', '')
        audio_file = request.files.get('audio')

        # 音声を一時保存
        audio_path = "temp_ai_response.wav"
        if audio_file:
            audio_file.save(audio_path)
            logging.info("Audio file received.")

        if conversation_log and GCS_BUCKET_NAME:
            try:
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                log_filename = f"logs/log_users/log_{timestamp}.txt"
                
                storage_client = storage.Client()
                bucket = storage_client.bucket(GCS_BUCKET_NAME)
                blob = bucket.blob(log_filename)
                
                blob.upload_from_string(conversation_log, content_type='text/plain')
                logging.info(f"Conversation log uploaded to gs://{GCS_BUCKET_NAME}/{log_filename}")
            except Exception as e:
                logging.error(f"Failed to upload log to GCS: {e}")

        # Gemini 1.5 Flash (マルチモーダル対応)
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-flash-latest')

        # 音声ファイルをアップロード
        uploaded_audio = None
        if os.path.exists(audio_path):
           uploaded_audio = genai.upload_file(audio_path, mime_type="audio/wav")

        prompt = f"""
        あなたは営業研修のコーチです。  
        
        【資料1】ユーザー（営業担当）の発言ログ:
        {conversation_log}

        【資料2】AI顧客の発言音声:
        (添付の音声ファイル)

        【指示】
        1. まず、添付の音声ファイル（AI顧客の発言）を聞き取り、内容を文字起こししてください。
        2. ユーザーの発言ログと合わせて、会話全体の流れを再現してください。
        3. その会話全体に基づいて、営業担当者のパフォーマンスを評価してください。

        【出力フォーマット】
        ## 会話の再現（要約）
        - 営業: ...
        - 顧客: ...

        ## フィードバック
        1. **良かった点**
        2. **改善点**
        3. **総合スコア** (/100)
        """

        contents = [prompt]
        if uploaded_audio:
            contents.append(uploaded_audio)

        response = model.generate_content(contents)
        
        # 後始末
        if os.path.exists(audio_path):
            os.remove(audio_path)

        return jsonify({"feedback": response.text})

    except Exception as e:
        logging.error(f"Feedback Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)