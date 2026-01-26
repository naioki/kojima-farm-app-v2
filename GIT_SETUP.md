# GitとGitHub連携ガイド

このプロジェクトをGitリポジトリとして初期化し、GitHubと連携する手順です。

## 前提条件

- Gitがインストールされていること
- GitHubアカウントがあること

## 手順

### 1. Gitリポジトリを初期化

プロジェクトフォルダで以下のコマンドを実行：

```bash
cd "g:\マイドライブ\00_CursorProject\01_Project\0126kojima-farm-app-v2"
git init
```

### 2. すべてのファイルをステージング

```bash
git add .
```

### 3. 初回コミット

```bash
git commit -m "Initial commit"
```

### 4. GitHubでリポジトリを作成

1. [GitHub](https://github.com)にログイン
2. 右上の「+」→「New repository」をクリック
3. リポジトリ名を入力（例: `kojima-farm-app-v2`）
4. 「Public」または「Private」を選択
5. **「Initialize this repository with a README」はチェックしない**（既にファイルがあるため）
6. 「Create repository」をクリック

### 5. リモートリポジトリを追加

GitHubでリポジトリを作成すると、以下のようなURLが表示されます：
```
https://github.com/あなたのユーザー名/kojima-farm-app-v2.git
```

このURLを使って、以下のコマンドを実行：

```bash
git remote add origin https://github.com/あなたのユーザー名/kojima-farm-app-v2.git
```

### 6. ブランチ名をmainに変更（必要に応じて）

```bash
git branch -M main
```

### 7. GitHubにプッシュ

```bash
git push -u origin main
```

GitHubの認証情報を求められたら、ユーザー名とパスワード（またはPersonal Access Token）を入力します。

## トラブルシューティング

### 認証エラーが出る場合

GitHubは2021年8月以降、パスワード認証を廃止しています。以下のいずれかの方法を使用してください：

#### 方法1: Personal Access Tokenを使用

1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. 「Generate new token (classic)」をクリック
3. スコープで `repo` にチェック
4. トークンを生成してコピー
5. パスワードの代わりにこのトークンを使用

#### 方法2: GitHub CLIを使用

```bash
gh auth login
```

#### 方法3: SSH鍵を使用

1. SSH鍵を生成（既にある場合はスキップ）
2. GitHub → Settings → SSH and GPG keys → New SSH key
3. 公開鍵を登録
4. リモートURLをSSH形式に変更：
```bash
git remote set-url origin git@github.com:あなたのユーザー名/kojima-farm-app-v2.git
```

### ファイルが大きすぎるエラー

`ipaexg.ttf`が大きすぎる場合（100MB以上）、Git LFSを使用するか、フォントファイルを別の方法で管理する必要があります。

### 既存のリポジトリと連携する場合

既にGitHubにリポジトリがある場合：

```bash
git remote add origin https://github.com/あなたのユーザー名/リポジトリ名.git
git branch -M main
git push -u origin main
```

## 今後の更新手順

ファイルを変更した後、GitHubに反映する手順：

```bash
# 変更を確認
git status

# 変更をステージング
git add .

# コミット
git commit -m "変更内容の説明"

# GitHubにプッシュ
git push
```

## 注意事項

- `.gitignore`に含まれているファイル（`config/email_config.json`など）はGitHubにアップロードされません
- `ipaexg.ttf`はリポジトリに含める必要があります（Streamlit Cloudで使用するため）
- 機密情報（APIキーなど）は`.gitignore`に追加するか、Streamlit CloudのSecretsを使用してください
