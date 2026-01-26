# GitHubにプッシュするコマンド

リモートリポジトリは正しく設定されています：
- origin: https://github.com/naioki/kojima-farm-app-v2

## 次のステップ：変更をプッシュ

以下のコマンドを順番に実行してください：

```powershell
# 1. 変更を確認
git status

# 2. すべての変更をステージング（packages.txtの削除を含む）
git add .

# 3. コミット
git commit -m "Remove packages.txt to fix Streamlit Cloud deployment"

# 4. GitHubにプッシュ
git push -u origin main
```

## もしブランチ名が異なる場合

```powershell
# 現在のブランチ名を確認
git branch

# ブランチ名をmainに変更（必要に応じて）
git branch -M main

# その後、プッシュ
git push -u origin main
```

## 認証について

プッシュ時に認証を求められた場合：

1. **ユーザー名**: `naioki`（またはGitHubのユーザー名）
2. **パスワード**: Personal Access Tokenを使用
   - GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
   - 「Generate new token (classic)」をクリック
   - スコープで `repo` にチェック
   - トークンを生成してコピー
   - パスワードの代わりにこのトークンを入力

## プッシュ後の確認

1. GitHubのリポジトリページで`packages.txt`が削除されていることを確認
2. Streamlit Cloudで自動的に再デプロイが開始されるのを待つ
3. または「Manage app」→「Reboot app」をクリック
