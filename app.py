import os
import json
import logging
import asyncio
import websockets
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock
from flask_cors import CORS
import google.generativeai as genai
import datetime
from google.cloud import storage

# 設定
app = Flask(__name__)
CORS(app)
sock = Sock(app)
logging.basicConfig(level=logging.INFO)

# APIキー
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# リアルタイム対話用モデル (実験版)
MODEL_NAME = "models/gemini-2.0-flash-exp"
# 環境変数からバケット名を取得（ファイルの冒頭付近）
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

@app.route('/')
def index():
    return render_template('index.html')

# ---------------------------------------------------------
# 🎤 リアルタイム対話の中継 (WebSocket Proxy)
# ---------------------------------------------------------
@sock.route('/ws/realtime')
def realtime_proxy(ws_client):
    host = "generativelanguage.googleapis.com"
    url = f"wss://{host}/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"

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
                # ★重要: 現在のAPI仕様に合わせて AUDIO のみに設定 (TEXTを含めるとエラーになるため)
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}}
                }
            }
        }
    }

    async def proxy_handler():
        try:
            async with websockets.connect(url) as ws_gemini:
                logging.info("Connected to Gemini")
                
                # 初期設定を送信
                await ws_gemini.send(json.dumps(setup_msg))
                
                # A. ブラウザ -> Gemini (マイク音声の転送)
                async def forward_to_gemini():
                    while True:
                        try:
                            # ★修正: ブロッキング回避のため別スレッドで受信
                            data = await asyncio.to_thread(ws_client.receive)
                            if data is None: 
                                break
                            await ws_gemini.send(data)
                        except Exception as e:
                            logging.error(f"Client->Gemini Error: {e}")
                            break

                # B. Gemini -> ブラウザ (AI音声の転送)
                async def forward_to_client():
                    async for msg in ws_gemini:
                        try:
                            # ★修正: バイト列なら文字列にデコードして送る
                            if isinstance(msg, bytes):
                                msg = msg.decode('utf-8')
                            ws_client.send(msg)
                        except Exception as e:
                            logging.error(f"Gemini->Client Error: {e}")
                            break

                # 送受信を並行して実行
                await asyncio.gather(forward_to_gemini(), forward_to_client())

        except Exception as e:
            logging.error(f"WebSocket Connection Error: {e}")
            try:
                ws_client.close()
            except:
                pass

    # Flask(同期)の中でAsyncio(非同期)を動かす
    try:
        asyncio.run(proxy_handler())
    except Exception as e:
        logging.error(f"Asyncio Error: {e}")


# ---------------------------------------------------------
# 📝 対話終了後の評価 & ログ保存
# ---------------------------------------------------------
@app.route('/feedback', methods=['POST'])
def feedback():
    try:
        data = request.json
        conversation_log = data.get('log', '')

        if not conversation_log:
            return jsonify({"feedback": "会話ログがありません。"}), 400

        # ★ GCSに保存する処理
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"logs/log_users/log_user_{timestamp}.txt" # GCS上のパス

        # GCSクライアント初期化 (環境変数の認証情報を使用)
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)

        # テキストデータを直接アップロード
        blob.upload_from_string(conversation_log, content_type='text/plain')
        
        logging.info(f"Log uploaded to gs://{BUCKET_NAME}/{filename}")
        # 評価用モデル (1.5 Flash - 安定版)
        genai.configure(api_key=GEMINI_API_KEY)
        # ユーザー環境に合わせてモデル名を指定
        model = genai.GenerativeModel('gemini-flash-latest')

        prompt = f"""
        あなたは営業研修のコーチです。
        以下の会話ログを分析し、フィードバックを行ってください。
        （ログにAIの言葉が含まれていない場合は、ユーザーの発言内容から文脈を推測してください）

        --- 会話ログ ---
        {conversation_log}
        ----------------

        ## 出力フォーマット
        1. **良かった点**
        2. **改善点** (具体的な言い回しの修正案)
        3. **成約の可能性** (％)
        4. **総合スコア** (/100)
        """

        response = model.generate_content(prompt)
        return jsonify({"feedback": response.text})

    except Exception as e:
        logging.error(f"Feedback Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)