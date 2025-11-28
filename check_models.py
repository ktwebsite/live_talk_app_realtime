import google.generativeai as genai
import os

# Dockerの環境変数からAPIキーを読み込む
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ APIキーが読み込めませんでした。.envを確認してください。")
else:
    genai.configure(api_key=api_key)
    print("🔍 利用可能なモデル一覧を取得中...")
    try:
        for m in genai.list_models():
            # 音声(generateContent)に対応しているモデルだけ表示
            if 'generateContent' in m.supported_generation_methods:
                print(f" - {m.name}")
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")