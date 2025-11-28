# import os
# import json
# import logging
# from flask import Flask, render_template, request, jsonify
# from flask_sock import Sock
# from flask_cors import CORS
# from google.cloud import storage
# import google.generativeai as genai
# import datetime

# # 設定と初期化
# app = Flask(__name__)
# CORS(app)
# sock = Sock(app)
# logging.basicConfig(level=logging.INFO)

# # 環境変数の読み込み (Dockerの場合はENVで渡す想定)
# PROJECT_ID = os.getenv("GCP_PROJECT_ID")
# BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# # GCPクライアント初期化
# # コンテナ内で service_account.json へのパスを環境変数で指定することを推奨
# storage_client = storage.Client()
# genai.configure(api_key=GEMINI_API_KEY)

# # 1. フロントエンド表示
# @app.route('/')
# def index():
#     return render_template('index.html')

# # 2. GCS署名付きURLの発行 (Upload用)
# @app.route('/sign-upload', methods=['POST'])
# def sign_upload():
#     try:
#         data = request.json
#         filename = f"uploads/{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}_{data.get('filename', 'audio.webm')}"
#         content_type = data.get('contentType', 'audio/webm')

#         bucket = storage_client.bucket(BUCKET_NAME)
#         blob = bucket.blob(filename)

#         # 署名付きURLの生成 (V4)
#         url = blob.generate_signed_url(
#             version="v4",
#             expiration=datetime.timedelta(minutes=15),
#             method="PUT",
#             content_type=content_type,
#         )
        
#         return jsonify({"upload_url": url, "public_url": blob.public_url, "gcs_uri": f"gs://{BUCKET_NAME}/{filename}"})
#     except Exception as e:
#         logging.error(f"Sign URL Error: {e}")
#         return jsonify({"error": str(e)}), 500

# # 3. 営業評価 (GenerateContent API)
# @app.route('/feedback', methods=['POST'])
# def feedback():
#     try:
#         data = request.json
#         gcs_uri = data.get('gcs_uri')
        
#         # gs://URI からファイルパスを抽出
#         # 例: gs://my-bucket/uploads/test.webm -> uploads/test.webm
#         if not gcs_uri.startswith(f"gs://{BUCKET_NAME}/"):
#             return jsonify({"error": "Invalid GCS URI"}), 400
            
#         blob_name = gcs_uri.replace(f"gs://{BUCKET_NAME}/", "")
#         local_filename = "/tmp/temp_audio.webm"

#         # 1. GCSからコンテナ内の一時フォルダにダウンロード
#         bucket = storage_client.bucket(BUCKET_NAME)
#         blob = bucket.blob(blob_name)
#         blob.download_to_filename(local_filename)
#         logging.info(f"Downloaded to {local_filename}")

#         # 2. GeminiのFile APIへアップロード
#         # (AI Studioはここを経由しないと音声ファイルを認識できません)
#         uploaded_file = genai.upload_file(local_filename, mime_type="audio/webm")
#         logging.info(f"Uploaded to Gemini: {uploaded_file.uri}")

#         # 3. プロンプト定義
#         prompt = """
#         あなたはベテランの営業マネージャーです。
#         提供された音声は、営業担当者の模擬セールストークです。
#         以下の観点でフィードバックをMarkdown形式で出力してください。
#         1. 良かった点
#         2. 改善すべき点（具体的に）
#         3. 総合スコア（100点満点）
#         """

#         # 4. 評価を実行 (モデル名は先ほど調べた最新のものにしてください)
#         # 例: gemini-2.0-flash または gemini-1.5-flash-latest など
#         model = genai.GenerativeModel('gemini-2.0-flash') 
        
#         response = model.generate_content([prompt, uploaded_file])
        
#         # 5. 後始末（一時ファイルの削除）
#         # ※Gemini側のファイルは自動で消えますが、ローカルは消しておくと良い
#         if os.path.exists(local_filename):
#             os.remove(local_filename)

#         return jsonify({"feedback": response.text})

#     except Exception as e:
#         logging.error(f"Feedback Error: {e}")
#         return jsonify({"error": str(e)}), 500

# # 4. Gemini Realtime API プロキシ (WebSocket)
# # 注: 実際のRealtime APIは双方向バイナリストリームの制御が必要で複雑なため、
# # ここでは疎通確認用のエコーロジックを実装します。
# @sock.route('/ws/realtime')
# def realtime_proxy(ws):
#     logging.info("WebSocket connected")
#     while True:
#         data = ws.receive()
#         if data is None:
#             break
#         # ここでGemini Realtime APIへ転送する処理が入ります
#         # 今回は受信したデータをそのまま返す（エコー）
#         ws.send(f"Server received: {len(data)} bytes")

# # 5. 一時APIキー（必要に応じて実装）
# @app.route('/get-key', methods=['GET'])
# def get_key():
#     # フロントエンドで直接APIを叩く必要がある場合に使用
#     return jsonify({"key": "TEMP_KEY_NOT_IMPLEMENTED_FOR_SECURITY"})

# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000, debug=True)
import os
import json
import logging
import asyncio
import websockets
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock
from flask_cors import CORS
import google.generativeai as genai

# 設定
app = Flask(__name__)
CORS(app)
sock = Sock(app)
logging.basicConfig(level=logging.INFO)

# 環境変数
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# 最新のGemini 2.0 Flashを使用（Live API対応）
MODEL_NAME = "models/gemini-2.0-flash-exp"

genai.configure(api_key=GEMINI_API_KEY)

@app.route('/')
def index():
    return render_template('index.html')

# ---------------------------------------------------------
# 🎤 リアルタイム対話の中継 (WebSocket Proxy)
# ---------------------------------------------------------
@sock.route('/ws/realtime')
def realtime_proxy(ws_client):
    """ブラウザとGemini Live APIを中継する"""
    api_url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"
    
    # 営業ロープレ用の設定（顧客役のペルソナ）
    # ここを変えれば、優しい客・厳しい客に変更可能
    setup_msg = {
        "setup": {
            "model": MODEL_NAME,
            "system_instruction": {
                "parts": [{"text": """
                あなたはIT企業の導入担当者（顧客）です。
                相手は営業担当者です。
                あなたは新しいツールの導入には慎重で、特に「コスト」と「セキュリティ」を気にしています。
                簡単には同意せず、鋭い質問を投げかけてください。
                ただし、相手の説明が論理的であれば納得してください。
                会話は日本語で行います。短めの返答を心がけてください。
                """}]
            },
            "generation_config": {
                "response_modalities": ["AUDIO"],  # 音声で返答させる
                "speech_config": {
                    "voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}} # 声の設定
                }
            }
        }
    }

    async def proxy_handler():
        async with websockets.connect(api_url) as ws_gemini:
            # 1. 初期設定を送信
            await ws_gemini.send(json.dumps(setup_msg))
            
            # 2. 初期メッセージ（AIから先に話させる場合）
            # await ws_gemini.send(json.dumps({"client_content": {"turns": [{"parts": [{"text": "こんにちは"}]}], "turn_complete": True}}))

            # 非同期タスク定義
            async def forward_to_gemini():
                """ブラウザ -> Gemini"""
                while True:
                    try:
                        data = ws_client.receive()
                        if data is None: break
                        
                        # ブラウザからのJSONをそのままGeminiへ転送
                        # (ブラウザ側でGeminiの形式に合わせて送信させる)
                        await ws_gemini.send(data)
                    except Exception as e:
                        logging.error(f"Client->Gemini Error: {e}")
                        break

            async def forward_to_client():
                """Gemini -> ブラウザ"""
                async for msg in ws_gemini:
                    try:
                        ws_client.send(msg)
                    except Exception as e:
                        logging.error(f"Gemini->Client Error: {e}")
                        break

            # 双方向通信を並行実行
            # Flask-Sock(スレッド)内でasyncioを回すための簡易ブリッジ
            await asyncio.gather(forward_to_gemini(), forward_to_client())

    # Flask(同期)の中でAsyncio(非同期)を実行
    try:
        asyncio.run(proxy_handler())
    except Exception as e:
        logging.error(f"WebSocket Proxy Error: {e}")


# ---------------------------------------------------------
# 📝 対話終了後の評価 (Feedback)
# ---------------------------------------------------------
@app.route('/feedback', methods=['POST'])
def feedback():
    try:
        # フロントエンドから会話ログ（テキスト）を受け取る
        # 注: 今回のLive APIは音声主体のため、厳密な文字起こしログを取るには
        # クライアント側で認識結果を貯めるか、Geminiのテキスト出力をパースする必要がある。
        # 今回は簡易的に「営業担当者が何と言ったか（自己申告）」または
        # 「どのような対話だったか」をGeminiに想像させて評価させる（簡易版）。
        
        # 本格実装では、リアルタイムAPIから返ってくる "text" 部分をクライアントで結合して送る。
        data = request.json
        conversation_log = data.get('log', '')

        if not conversation_log:
            return jsonify({"feedback": "会話ログが空のため評価できませんでした。"}), 400

        prompt = f"""
        あなたは営業研修のコーチです。
        以下は、営業担当者と顧客（AI）の会話ログです。
        この商談を評価してください。

        --- 会話ログ ---
        {conversation_log}
        ----------------

        ## フィードバック形式
        1. **良かった点**
        2. **改善点** (具体的なフレーズの提案を含む)
        3. **成約の可能性** (％)
        4. **総合スコア** (/100)
        """

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        
        return jsonify({"feedback": response.text})

    except Exception as e:
        logging.error(f"Feedback Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)