# Streamlit Cloud トラブルシューティング

## Error installing requirements エラー

### 確認事項

1. **requirements.txtの配置場所**
   - `requirements.txt` はリポジトリのルート（`app.py`と同じディレクトリ）に配置されているか確認

2. **ファイル名の確認**
   - `requirements.txt`（拡張子は`.txt`）
   - 大文字小文字を確認（`Requirements.txt`は不可）

3. **Streamlit Cloudのログを確認**
   - Streamlit Cloudのアプリページで「Manage app」をクリック
   - 「Logs」タブを開いてエラーの詳細を確認

### よくある問題と解決策

#### 問題1: パッケージが見つからない

**エラーメッセージ例:**
```
ERROR: Could not find a version that satisfies the requirement xxx
```

**解決策:**
- パッケージ名のスペルミスを確認
- バージョン指定を緩和（例: `>=4.0.0` → `>=4.0`）

#### 問題2: Pythonのバージョン互換性

**解決策:**
- Streamlit CloudはPython 3.8以上をサポート
- 古いバージョン指定を削除

#### 問題3: reportlabのインストールエラー

**解決策:**
`requirements.txt`に以下が含まれているか確認:
```
reportlab>=4.0.0
```

### 修正済みのrequirements.txt

現在の`requirements.txt`は以下の通りです：

```
streamlit>=1.28.0
google-generativeai>=0.3.0
reportlab>=4.0.0
Pillow>=10.0.0
pandas>=2.0.0
```

### デバッグ手順

1. **ローカルでテスト**
   ```bash
   pip install -r requirements.txt
   streamlit run app.py
   ```
   ローカルで動作することを確認

2. **Streamlit Cloudのログを確認**
   - 「Manage app」→「Logs」でエラー詳細を確認
   - エラーメッセージをコピー

3. **必要に応じてrequirements.txtを修正**
   - エラーが出ているパッケージのバージョンを調整
   - またはバージョン指定を削除

### 代替案: 最小限のrequirements.txt

もし上記で解決しない場合、以下の最小限のバージョンで試してください：

```
streamlit
google-generativeai
reportlab
Pillow
pandas
```

### その他の確認事項

- ✅ `requirements.txt`がリポジトリに含まれている
- ✅ `app.py`がリポジトリのルートにある
- ✅ すべてのPythonファイルが正しくエンコードされている（UTF-8）
- ✅ フォントファイル（`ipaexg.ttf`）がリポジトリに含まれている
