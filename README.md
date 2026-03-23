# EvexBot セットアップマニュアル

EvexDevelopers Discord サーバー用の多機能Bot。

## 必要なもの

- Python 3.12 (pyenv推奨)
- libsqlite3-dev (Debian/Ubuntu)
- Discord Bot Token

## Discord Bot の作り方

1. [Discord Developer Portal](https://discord.com/developers/applications) にアクセス
2. 「New Application」でアプリケーションを作成
3. 左メニュー「Bot」を開く
4. 「Reset Token」でトークンを取得（一度しか表示されないのでコピーしておく）
5. 以下の Privileged Gateway Intents を全てONにする:
   - Presence Intent
   - Server Members Intent
   - Message Content Intent
6. 左メニュー「OAuth2」→「URL Generator」を開く
7. SCOPES: `bot`, `applications.commands` にチェック
8. BOT PERMISSIONS: `Administrator` にチェック（または必要な権限を個別に選択）
9. 生成されたURLをブラウザで開いてサーバーに招待

## 環境構築

### 1. システム依存パッケージのインストール (Debian/Ubuntu)

```bash
sudo apt install libsqlite3-dev
```

### 2. Python のインストール (pyenv)

```bash
# pyenvが未インストールの場合
curl https://pyenv.run | bash

# Python 3.12をインストール（libsqlite3-devの後にやること）
pyenv install 3.12.13
```

既にPythonをインストール済みで `_sqlite3` エラーが出る場合は再ビルドが必要:

```bash
pyenv install 3.12.13 --force
```

### 3. リポジトリのクローンとセットアップ

```bash
git clone https://github.com/serkenn/EvexBot.git
cd EvexBot
pyenv local 3.12.13
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. 環境変数の設定

```bash
cp .env.example .env
```

`.env` を編集して `DISCORD_TOKEN` に取得したトークンを設定する。
その他のIDは自分のサーバーに合わせて変更する。

```
DISCORD_TOKEN=あなたのトークン
LINK_CHANNEL_ID=リンク収集対象のチャンネルID
INTRO_CHANNEL_ID=自己紹介チャンネルID
ADMIN_ROLE_ID=管理者ロールID
ADMIN_USER_ID=管理者ユーザーID
```

チャンネルIDやロールIDの取得方法:
Discord設定 → 詳細設定 → 開発者モードをON → 対象を右クリック → 「IDをコピー」

### 5. 起動

```bash
python bot.py
```

## Cog 一覧

| ファイル | 機能 |
|---|---|
| `welcome.py` | メンバー参加/退室メッセージ、マイルストーン祝い |
| `growth.py` | サーバー成長予測 (多項式回帰/Prophet) |
| `members-history.py` | メンバー数推移グラフ、本日の増加数表示 |
| `zikosyokai.py` | 自己紹介チャンネルのテンプレート自動管理 |
| `linkapi.py` | チャンネルのリンク収集REST API |
| `messagelink.py` | メッセージリンク展開 |
| `mvp.py` | MVP選出 |
| `avatar.py` | アバター表示 |
| `imagegen.py` | 画像生成 |
| `sandbox.py` | サンドボックス |

## スラッシュコマンド

| コマンド | 説明 |
|---|---|
| `/members-history` | 指定期間のメンバー数推移をグラフ表示 |
| `/members-today` | 本日の新規メンバー数と昨日・先週比を表示 |
| `/growth` | サーバーの成長予測 |
| `/welcome` | 参加メッセージのON/OFF設定 |
| `/leave-message` | 退室メッセージのON/OFF設定 |

## 設定

`config.yml` でプレフィックスコマンドの接頭辞を変更できる:

```yaml
prefix: "ev?"
```
