import os
from google.cloud import storage

# .envファイルを読み込む簡易ロジック
# (docker runで環境変数が渡されているので、スクリプト内で直接取得します)

def set_cors_configuration(bucket_name):
    """バケットのCORS設定を更新して、localhostからのアップロードを許可する"""
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)

    # CORS設定の定義
    cors_configuration = [
        {
            "origin": ["*"],  # 開発用なので全許可（本番では http://localhost:5000 等に絞る）
            "method": ["GET", "PUT", "POST", "OPTIONS"],
            "responseHeader": ["*"],
            "maxAgeSeconds": 3600
        }
    ]

    bucket.cors = cors_configuration
    bucket.patch()

    print(f"✅ バケット {bucket_name} のCORS設定を更新しました！")
    print(f"現在の設定: {bucket.cors}")

if __name__ == "__main__":
    # 環境変数からバケット名を取得
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    
    if not bucket_name:
        print("❌ エラー: GCS_BUCKET_NAME が見つかりません。")
    else:
        set_cors_configuration(bucket_name)