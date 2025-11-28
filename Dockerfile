# ベースイメージ
FROM python:3.10-slim

# 作業ディレクトリ
WORKDIR /app

# 依存関係インストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードのコピー
COPY . .

# 環境変数（デフォルト値、実行時に上書き推奨）
ENV FLASK_APP=app.py

# ポート公開
EXPOSE 5000

# 実行コマンド
CMD ["python", "app.py"]