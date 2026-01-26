# 出荷ラベル生成アプリ

FAX注文書画像をアップロードすると、Gemini APIで解析し、店舗ごとの出荷ラベルPDFを生成するStreamlitアプリです。

## 機能

- 📸 FAX注文書画像のアップロード
- 🤖 Gemini APIによる自動解析（複数店舗対応）
- ✏️ 解析結果の確認・編集
- 📅 出荷日時の入力
- 📄 A4用紙に8分割（2列x4段）のPDF生成（複数ページ対応）
- 📊 出荷一覧表の自動生成（PDFの最初のページ）
- 🎯 通常箱と端数箱（最後の1箱）の自動判別
- ✂️ 切断用ガイド線の自動描画

## セットアップ

### Streamlit Cloudでデプロイする場合

**詳細な手順は `DEPLOY.md` を参照してください。**

#### 簡単な手順

1. **GitHubにリポジトリを作成・アップロード**
   - 方法1: GitHub Web UIで手動アップロード（簡単）
   - 方法2: Gitコマンドを使用（推奨）
   - 詳細は `DEPLOY.md` を参照

2. **Streamlit Cloudでアプリをデプロイ**
   - [Streamlit Cloud](https://streamlit.io/cloud)にアクセス
   - GitHubアカウントでログイン
   - 「New app」をクリック
   - リポジトリを選択
   - Main file path: `app.py`
   - 「Deploy!」をクリック

3. **Secretsの設定（オプション - メール機能を使う場合）**
   - Streamlit Cloudのアプリ設定で「Secrets」を開く
   - 以下の形式で設定:
   ```toml
   [email]
   imap_server = "imap.gmail.com"
   email_address = "your-email@gmail.com"
   sender_email = "sender@example.com"
   days_back = 1
   ```
   - **注意**: Gemini APIキーはSecretsに保存せず、アプリ内で入力してください（セキュリティのため）

4. **重要な確認事項**
   - ✅ `ipaexg.ttf` がリポジトリに含まれていること（必須）
   - ✅ `requirements.txt` が正しく設定されていること
   - ✅ `.streamlit/config.toml` が含まれていること

### ローカルで実行する場合

#### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

#### 2. IPAexGothicフォントの準備

ReportLabで日本語を表示するために、IPAexGothicフォントが必要です。

**プロジェクトルートに `ipaexg.ttf` を配置**（推奨）

または、以下のパスに配置:
- `fonts/ipaexg.ttf`
- Windows: `C:/Windows/Fonts/ipaexg.ttf`
- Linux: `/usr/share/fonts/ipaexg.ttf`

#### 3. Gemini APIキーの取得

1. [Google AI Studio](https://makersuite.google.com/app/apikey) でAPIキーを取得
2. アプリ起動後、サイドバーでAPIキーを入力

## 使い方

1. アプリを起動
   ```bash
   streamlit run app.py
   ```

2. サイドバーでGemini APIキーを入力

3. 出荷日を選択（デフォルトは明日）

4. 注文書画像をアップロード（PNG、JPG、JPEG形式）

5. 「画像を解析」ボタンをクリック

6. 解析結果を確認・編集（テーブルで編集可能）

7. 「ラベルを生成」ボタンをクリック

8. 「PDFを生成」ボタンをクリックしてPDFをダウンロード

## ラベル仕様

### 通常箱ラベル
- 店舗名（上部中央、24pt）
- 品目（中央やや上、18pt）
- 入り数（中央、28pt）
- 出荷日（左下、12pt）
- 通し番号（右下、14pt）

### 端数箱ラベル（最後の1箱）
- **太い黒の破線枠**で囲む
- **数量を超巨大フォント（48pt）**で中央に表示
- 店舗名・品目は小さく（12pt）上部に配置
- 出荷日（左下、10pt）
- 通し番号（右下、10pt）

### 用紙仕様
- A4縦（210mm × 297mm）
- 2列×4段の8分割
- 1ラベルサイズ：105mm × 74.25mm
- 切断用ガイド線：極めて薄いグレー（透明度15%）、間隔の広い破線
- **複数ページ対応**：8個を超えるラベルは自動的に複数ページに分割

### 出荷一覧表
- PDFの最初のページに自動生成
- 店舗名、品目、フル箱数、端数箱数、TOTALパック数を表示

## ファイル構成

```
.
├── app.py                      # Streamlitメインアプリケーション
├── pdf_generator.py            # PDF生成ロジック（複数ページ対応）
├── config_manager.py           # 店舗名・品目名の管理
├── email_reader.py             # メール自動読み取り機能
├── email_config_manager.py     # メール設定管理
├── requirements.txt            # 依存パッケージ
├── ipaexg.ttf                  # IPAexGothicフォント（日本語表示用）
├── .streamlit/
│   └── config.toml            # Streamlit設定
├── .gitignore                  # Git除外ファイル
├── config/                     # 設定ファイル（自動生成）
│   ├── stores.json            # 店舗名リスト
│   └── items.json             # 品目名リスト
└── README.md                   # このファイル
```

## 主な改善点

### 既存リポジトリ（kojima-farm-app）からの変更点

1. **複数店舗対応**
   - 1つの注文書から複数店舗のデータを抽出
   - 店舗ごとにラベルを自動生成

2. **複数ページ対応**
   - 8個を超えるラベルも自動的に複数ページに分割
   - すべての店舗のラベルが印刷される

3. **出荷一覧表の追加**
   - PDFの最初のページに出荷一覧表を自動生成
   - 店舗ごとの集計情報を表示

4. **出荷日時入力**
   - ユーザーが自由に出荷日を選択可能
   - ラベルに出荷日が表示される

5. **差し札形式**
   - コンテナに入れる差し札として最適化
   - 横長デザイン（105mm × 74.25mm）

## 注意事項

- Gemini APIの利用にはAPIキーが必要です（有料プランになる可能性があります）
- フォントファイル（ipaexg.ttf）が見つからない場合、日本語が正しく表示されない可能性があります
- 店舗ごとにすべてのラベルが印刷されます（複数ページに自動分割）
- Streamlit Cloudでデプロイする場合、`ipaexg.ttf` がリポジトリに含まれている必要があります
- メール機能を使用する場合、Streamlit CloudのSecretsで設定できますが、パスワードは毎回入力が必要です（セキュリティのため）

## トラブルシューティング

### フォントが見つからないエラー
- `fonts/ipaexg.ttf` または `ipaexg.ttf` が存在するか確認
- `pdf_generator.py` の `_find_font_path()` でフォントパスを確認・修正

### Gemini APIエラー
- APIキーが正しいか確認
- インターネット接続を確認
- APIの利用制限に達していないか確認

### PDFが生成されない
- ラベルが正しく生成されているか確認（「ラベルを生成」ボタンで確認）
- エラーメッセージを確認
- データが正しく入力されているか確認

### 複数店舗が正しく解析されない
- 画像の鮮明さを確認
- 解析結果を手動で編集・追加可能
- テーブルで直接編集できます
