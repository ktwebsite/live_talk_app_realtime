FROM python:3.10-slim

WORKDIR /app

# ライブラリのインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# コードのコピー
COPY . .

# 環境変数の準備
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1

# ポート5000を開放
EXPOSE 5000

# 起動コマンド
CMD ["python", "app.py"]