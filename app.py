import os
import json
import logging
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock
from flask_cors import CORS
from google.cloud import storage
import google.generativeai as genai
import datetime

# 設定と初期化
app = Flask(__name__)
CORS(app)
sock = Sock(app)
logging.basicConfig(level=logging.INFO)

# 環境変数の読み込み (Dockerの場合はENVで渡す想定)
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# GCPクライアント初期化
# コンテナ内で service_account.json へのパスを環境変数で指定することを推奨
storage_client = storage.Client()
genai.configure(api_key=GEMINI_API_KEY)

# 1. フロントエンド表示
@app.route('/')
def index():
    return render_template('index.html')

# 2. GCS署名付きURLの発行 (Upload用)
@app.route('/sign-upload', methods=['POST'])
def sign_upload():
    try:
        data = request.json
        filename = f"uploads/{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}_{data.get('filename', 'audio.webm')}"
        content_type = data.get('contentType', 'audio/webm')

        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)

        # 署名付きURLの生成 (V4)
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=15),
            method="PUT",
            content_type=content_type,
        )
        
        return jsonify({"upload_url": url, "public_url": blob.public_url, "gcs_uri": f"gs://{BUCKET_NAME}/{filename}"})
    except Exception as e:
        logging.error(f"Sign URL Error: {e}")
        return jsonify({"error": str(e)}), 500

# 3. 営業評価 (GenerateContent API)
@app.route('/feedback', methods=['POST'])
def feedback():
    try:
        data = request.json
        gcs_uri = data.get('gcs_uri')
        
        # gs://URI からファイルパスを抽出
        # 例: gs://my-bucket/uploads/test.webm -> uploads/test.webm
        if not gcs_uri.startswith(f"gs://{BUCKET_NAME}/"):
            return jsonify({"error": "Invalid GCS URI"}), 400
            
        blob_name = gcs_uri.replace(f"gs://{BUCKET_NAME}/", "")
        local_filename = "/tmp/temp_audio.webm"

        # 1. GCSからコンテナ内の一時フォルダにダウンロード
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.download_to_filename(local_filename)
        logging.info(f"Downloaded to {local_filename}")

        # 2. GeminiのFile APIへアップロード
        # (AI Studioはここを経由しないと音声ファイルを認識できません)
        uploaded_file = genai.upload_file(local_filename, mime_type="audio/webm")
        logging.info(f"Uploaded to Gemini: {uploaded_file.uri}")

        # 3. プロンプト定義
        prompt = """
        あなたはベテランの営業マネージャーです。
        提供された音声は、営業担当者の模擬セールストークです。
        以下の観点でフィードバックをMarkdown形式で出力してください。
        1. 良かった点
        2. 改善すべき点（具体的に）
        3. 総合スコア（100点満点）
        """

        # 4. 評価を実行 (モデル名は先ほど調べた最新のものにしてください)
        # 例: gemini-2.0-flash または gemini-1.5-flash-latest など
        model = genai.GenerativeModel('gemini-2.0-flash') 
        
        response = model.generate_content([prompt, uploaded_file])
        
        # 5. 後始末（一時ファイルの削除）
        # ※Gemini側のファイルは自動で消えますが、ローカルは消しておくと良い
        if os.path.exists(local_filename):
            os.remove(local_filename)

        return jsonify({"feedback": response.text})

    except Exception as e:
        logging.error(f"Feedback Error: {e}")
        return jsonify({"error": str(e)}), 500

# 4. Gemini Realtime API プロキシ (WebSocket)
# 注: 実際のRealtime APIは双方向バイナリストリームの制御が必要で複雑なため、
# ここでは疎通確認用のエコーロジックを実装します。
@sock.route('/ws/realtime')
def realtime_proxy(ws):
    logging.info("WebSocket connected")
    while True:
        data = ws.receive()
        if data is None:
            break
        # ここでGemini Realtime APIへ転送する処理が入ります
        # 今回は受信したデータをそのまま返す（エコー）
        ws.send(f"Server received: {len(data)} bytes")

# 5. 一時APIキー（必要に応じて実装）
@app.route('/get-key', methods=['GET'])
def get_key():
    # フロントエンドで直接APIを叩く必要がある場合に使用
    return jsonify({"key": "TEMP_KEY_NOT_IMPLEMENTED_FOR_SECURITY"})

if __name__ == '__main__':
     app.run(host='0.0.0.0', port=5000, debug=True)
