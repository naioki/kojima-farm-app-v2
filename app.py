<<<<<<< HEAD
"""
出荷ラベル生成Streamlitアプリ
FAX注文書画像をアップロードして、店舗ごとの出荷ラベルPDFを生成
"""
import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
from pdf_generator import LabelPDFGenerator
import tempfile
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
import re
import traceback

# 設定管理モジュールのインポート
from config_manager import (
    load_stores, save_stores, add_store, remove_store,
    load_items, save_items, add_item_variant, add_new_item, remove_item,
    auto_learn_store, auto_learn_item
)
from email_config_manager import load_email_config, save_email_config, detect_imap_server
from email_reader import check_email_for_orders

# ページ設定
st.set_page_config(
    page_title="出荷ラベル生成アプリ",
    page_icon="📦",
    layout="wide"
)

# セッション状態の初期化
if 'api_key' not in st.session_state:
    st.session_state.api_key = ''
if 'parsed_data' not in st.session_state:
    st.session_state.parsed_data = None
if 'labels' not in st.session_state:
    st.session_state.labels = []
if 'shipment_date' not in st.session_state:
    st.session_state.shipment_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
if 'image_uploaded' not in st.session_state:
    st.session_state.image_uploaded = None
if 'email_config' not in st.session_state:
    # st.secretsに安全にアクセス（secretsファイルが存在しない場合でもエラーにならないように）
    try:
        secrets_obj = st.secrets if hasattr(st, 'secrets') else None
    except Exception:
        secrets_obj = None
    st.session_state.email_config = load_email_config(secrets_obj)
if 'email_password' not in st.session_state:
    st.session_state.email_password = ""


def safe_int(v):
    """安全に整数に変換"""
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    s = re.sub(r'\D', '', str(v))
    return int(s) if s else 0


def get_known_stores():
    """店舗名リストを取得（動的）"""
    return load_stores()


def get_item_normalization():
    """品目名正規化マップを取得（動的）"""
    return load_items()


def normalize_item_name(item_name, auto_learn=True):
    """品目名を正規化する（動的設定対応）"""
    if not item_name:
        return ""
    item_name = str(item_name).strip()
    item_normalization = get_item_normalization()
    
    for normalized, variants in item_normalization.items():
        if item_name in variants or any(variant in item_name for variant in variants):
            return normalized
    
    # 見つからない場合、自動学習
    if auto_learn:
        return auto_learn_item(item_name)
    return item_name


def validate_store_name(store_name, auto_learn=True):
    """店舗名を検証し、最も近い店舗名を返す（動的設定対応）"""
    if not store_name:
        return None
    store_name = str(store_name).strip()
    known_stores = get_known_stores()
    
    # 完全一致
    if store_name in known_stores:
        return store_name
    # 部分一致
    for known_store in known_stores:
        if known_store in store_name or store_name in known_store:
            return known_store
    
    # 見つからない場合、自動学習
    if auto_learn:
        return auto_learn_store(store_name)
    return None


def parse_order_image(image: Image.Image, api_key: str) -> list:
    """
    Gemini APIで注文書画像を解析（複数店舗対応）
    
    Args:
        image: PIL Imageオブジェクト
        api_key: Gemini APIキー
    
    Returns:
        解析結果のリスト [{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}]
    """
    genai.configure(api_key=api_key)
    
    # モデルを初期化（gemini-2.0-flashを優先）
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
    except:
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
        except:
            try:
                model = genai.GenerativeModel('gemini-1.5-pro')
            except:
                model = genai.GenerativeModel('gemini-pro-vision')
    
    # 店舗名・品目名リストを取得
    known_stores = get_known_stores()
    item_normalization = get_item_normalization()
    store_list = "、".join(known_stores)
    item_list = ", ".join(item_normalization.keys())
    
    # プロンプト（既存リポジトリを参考）
    prompt = f"""
画像を解析し、以下の厳密なルールに従ってJSONで返してください。

【店舗名リスト（参考）】
{store_list}
※上記リストにない店舗名も読み取ってください。

【品目名の正規化ルール】
{json.dumps(item_normalization, ensure_ascii=False, indent=2)}

【重要ルール】
1. 店舗名の後に「:」または改行がある場合、その後の行は全てその店舗の注文です
2. 品目名がない行（例：「50×1」）は、直前の品目の続きとして処理してください
3. 「/」で区切られた複数の注文は、同じ店舗・同じ品目として統合してください
   - 例：「胡瓜バラ100×7 / 50×1」→ 胡瓜バラ100本×7箱 + 端数50本
4. 「胡瓜バラ」と「胡瓜3本」は別の規格として扱ってください
5. unit, boxes, remainderには「数字のみ」を入れてください

【計算ルール】
- 胡瓜(3本P): 30本/箱 → unit=30
- 胡瓜(バラ): 100本/箱（50本以上なら50本箱1、未満はバラ）→ unit=100
- 春菊: 30袋/箱 → unit=30
- 青梗菜: 20袋/箱 → unit=20
- 長ネギ(2本P): 30本/箱 → unit=30

【最重要：総数（パック数）の表記について】
- 「×数字」の表記（例：「×180」「×100」「×50」）は「総数（パック数）」を意味します
- 「×数字」は「箱数」ではなく「総数」です！絶対に間違えないでください！
- この場合、unit（1箱あたりの入数）とboxes（箱数）を逆算してください
- 計算式：総数 = unit × boxes + remainder
- 総数がunitで割り切れる場合：boxes = 総数 ÷ unit, remainder = 0
- 総数がunitで割り切れない場合：boxes = 総数 ÷ unit（切り捨て）, remainder = 総数 - (unit × boxes)

【数量計算の例（重要：×数字は総数を意味する）】
- 「胡瓜3本×180」→ 総数180パック = unit=30の場合、boxes=6, remainder=0 (180÷30=6箱)
- 「胡瓜3本×100」→ 総数100パック = unit=30の場合、boxes=3, remainder=10 (100÷30=3箱余り10)
- 「胡瓜3本×60」→ 総数60パック = unit=30の場合、boxes=2, remainder=0 (60÷30=2箱)
- 「胡瓜3本×30」→ 総数30パック = unit=30の場合、boxes=1, remainder=0 (30÷30=1箱)
- 「胡瓜3本×20」→ 総数20パック = unit=30の場合、boxes=0, remainder=20 (20<30なので端数のみ)
- 「春菊×50」→ 総数50パック = unit=30の場合、boxes=1, remainder=20 (50÷30=1箱余り20)
- 「ネギ2本×80」→ 総数80パック = unit=30の場合、boxes=2, remainder=20 (80÷30=2箱余り20)
- 「胡瓜バラ100×7 / 50×1」→ これは特殊な表記：100本/箱×7箱 + 端数50本 = unit=100, boxes=7, remainder=50

【出力JSON形式】
[{{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}}]

必ず全ての店舗と品目を漏れなく読み取ってください。
"""
    
    try:
        response = model.generate_content([prompt, image])
        # レスポンスからJSONを抽出
        text = response.text.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            parts = text.split('```')
            for part in parts:
                if '{' in part and '[' in part:
                    text = part.strip()
                    break
        
        # JSONをパース
        result = json.loads(text)
        # リストでない場合はリストに変換
        if isinstance(result, dict):
            result = [result]
        return result
    except json.JSONDecodeError as e:
        st.error(f"JSON解析エラー: {e}")
        st.text(f"レスポンス内容: {text[:500]}")
        return None
    except Exception as e:
        st.error(f"画像解析エラー: {e}")
        return None


def validate_and_fix_order_data(order_data, auto_learn=True):
    """AIが読み取ったデータを検証し、必要に応じて修正する（自動学習対応）"""
    if not order_data:
        return []
    
    validated_data = []
    errors = []
    learned_stores = []
    learned_items = []
    
    known_stores = get_known_stores()
    
    for i, entry in enumerate(order_data):
        # 必須フィールドのチェック
        store = entry.get('store', '').strip()
        item = entry.get('item', '').strip()
        
        # 店舗名の検証と修正（自動学習）
        validated_store = validate_store_name(store, auto_learn=auto_learn)
        if not validated_store and store:
            if auto_learn:
                validated_store = auto_learn_store(store)
                if validated_store not in learned_stores:
                    learned_stores.append(validated_store)
            else:
                errors.append(f"行{i+1}: 不明な店舗名「{store}」")
                # 最も近い店舗名を推測
                for known_store in known_stores:
                    if any(char in store for char in known_store):
                        validated_store = known_store
                        break
        
        # 品目名の正規化（自動学習）
        normalized_item = normalize_item_name(item, auto_learn=auto_learn)
        if not normalized_item and item:
            if auto_learn:
                normalized_item = auto_learn_item(item)
                if normalized_item not in learned_items:
                    learned_items.append(normalized_item)
            else:
                errors.append(f"行{i+1}: 品目名「{item}」を正規化できませんでした")
        
        # 数量の検証
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        
        # 数量が0の場合は警告
        if unit == 0 and boxes == 0 and remainder == 0:
            errors.append(f"行{i+1}: 数量が全て0です（店舗: {store}, 品目: {item}）")
        
        # 検証済みデータを追加
        spec_value = entry.get('spec', '')
        if spec_value is None:
            spec_value = ''
        else:
            spec_value = str(spec_value).strip()
        
        validated_entry = {
            'store': validated_store or store,
            'item': normalized_item or item,
            'spec': spec_value,
            'unit': unit,
            'boxes': boxes,
            'remainder': remainder
        }
        validated_data.append(validated_entry)
    
    # 自動学習の結果を表示
    if auto_learn:
        if learned_stores:
            st.success(f"✨ 新しい店舗名を学習しました: {', '.join(learned_stores)}")
        if learned_items:
            st.success(f"✨ 新しい品目名を学習しました: {', '.join(learned_items)}")
    
    # エラーがある場合は表示
    if errors:
        st.warning("⚠️ 検証で以下の問題が見つかりました:")
        for error in errors:
            st.write(f"- {error}")
    
    return validated_data


def generate_labels_from_data(order_data: list, shipment_date: str) -> list:
    """
    解析データからラベルリストを生成（店舗ごと）
    
    Args:
        order_data: 解析結果のリスト [{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}]
        shipment_date: 出荷日（YYYY-MM-DD形式）
    
    Returns:
        ラベル情報のリスト
    """
    labels = []
    shipment_date_display = datetime.strptime(shipment_date, '%Y-%m-%d').strftime('%m月%d日')
    
    for entry in order_data:
        store = entry.get('store', '')
        item = entry.get('item', '')
        spec = entry.get('spec', '')
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        
        if unit == 0:
            continue
        
        # 単位を判定（パックと袋は「袋」に統一）
        unit_label = '本'
        if '春菊' in item or '青梗菜' in item or 'チンゲン菜' in item:
            unit_label = '袋'
        elif 'ネギ' in item or 'ねぎ' in item:
            unit_label = '袋'  # パックから袋に統一
        elif '胡瓜' in item or 'きゅうり' in item:
            if 'バラ' in spec or 'ばら' in spec:
                unit_label = '本'
            else:
                unit_label = '袋'  # パックから袋に統一
        
        # 通常箱のラベル
        total_boxes = boxes + (1 if remainder > 0 else 0)
        for i in range(boxes):
            labels.append({
                'store': store,
                'item': item,
                'spec': spec,
                'quantity': f"{unit}{unit_label}",
                'sequence': f"{i+1}/{total_boxes}",
                'is_fraction': False,
                'shipment_date': shipment_date_display,
                'unit': unit,
                'boxes': boxes,
                'remainder': remainder
            })
        
        # 端数箱のラベル（余りがある場合）
        if remainder > 0:
            labels.append({
                'store': store,
                'item': item,
                'spec': spec,
                'quantity': f"{remainder}{unit_label}",
                'sequence': f"{total_boxes}/{total_boxes}",
                'is_fraction': True,
                'shipment_date': shipment_date_display,
                'unit': unit,
                'boxes': boxes,
                'remainder': remainder
            })
    
    return labels


def get_unit_label_for_item(item: str, spec: str) -> str:
    """
    品目名と規格から単位を判定
    
    Args:
        item: 品目名
        spec: 規格
    
    Returns:
        単位（'本'、'袋'など）
    """
    item_lower = item.lower() if item else ""
    spec_lower = spec.lower() if spec else ""
    
    # 単位を判定（パックと袋は「袋」に統一）
    unit_label = '本'
    if '春菊' in item or '青梗菜' in item or 'チンゲン菜' in item:
        unit_label = '袋'
    elif 'ネギ' in item or 'ねぎ' in item:
        unit_label = '袋'  # パックから袋に統一
    elif '胡瓜' in item or 'きゅうり' in item:
        if 'バラ' in spec or 'ばら' in spec_lower:
            unit_label = '本'
        else:
            unit_label = '袋'  # パックから袋に統一
    
    return unit_label


def generate_summary_table(order_data: list) -> list:
    """
    出荷一覧表用のデータを生成
    
    Args:
        order_data: 解析結果のリスト
    
    Returns:
        一覧表用のデータリスト
    """
    summary = []
    for entry in order_data:
        store = entry.get('store', '')
        item = entry.get('item', '')
        spec = entry.get('spec', '')
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        unit = safe_int(entry.get('unit', 0))
        
        rem_box = 1 if remainder > 0 else 0
        total_packs = boxes + rem_box  # フル箱 + 端数箱 = パック数
        total_quantity = (unit * boxes) + remainder  # 総数量
        
        # 単位を判定
        unit_label = get_unit_label_for_item(item, spec)
        
        summary.append({
            'store': store,
            'item': item,
            'spec': spec,
            'boxes': boxes,
            'rem_box': rem_box,
            'total_packs': total_packs,
            'total_quantity': total_quantity,
            'unit': unit,
            'unit_label': unit_label  # 単位情報を追加
        })
    
    return summary


def generate_line_summary(order_data: list) -> str:
    """
    LINEに貼り付け可能な集計テキストを生成
    
    Args:
        order_data: 解析結果のリスト
    
    Returns:
        LINE用の集計テキスト
    """
    summary_packs = defaultdict(int)
    
    for entry in order_data:
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        total = (unit * boxes) + remainder
        
        # キーをitemとspecの組み合わせにする（胡瓜の3本Pとバラを別物として扱う）
        item = entry.get('item', '不明')
        spec = entry.get('spec', '').strip()
        key = (item, spec)  # タプルをキーとして使用
        summary_packs[key] += total
    
    # 単位判定関数（共通関数を使用）
    
    line_text = f"【{datetime.now().strftime('%m/%d')} 出荷・作成総数】\n"
    # キーをソートして表示（品目名→規格の順）
    sorted_items = sorted(summary_packs.items(), key=lambda x: (x[0][0], x[0][1]))
    for (item, spec), total in sorted_items:
        unit_label = get_unit_label_for_item(item, spec)
        # 表示形式: 品目名(規格)：数量単位
        if spec:
            display_name = f"{item}({spec})"
        else:
            display_name = item
        line_text += f"・{display_name}：{total}{unit_label}\n"
    
    return line_text


# メインUI
st.title("📦 出荷ラベル生成アプリ")
st.markdown("FAX注文書画像をアップロードして、店舗ごとの出荷ラベルPDFを生成します。")

# タブ作成
tab1, tab2, tab3 = st.tabs(["📸 画像解析", "📧 メール自動読み取り", "⚙️ 設定管理"])

# サイドバー
with st.sidebar:
    st.header("⚙️ 設定")
    
    api_key = st.text_input(
        "Gemini APIキー",
        value=st.session_state.api_key,
        type="password",
        help="Google Gemini APIのキーを入力してください"
    )
    st.session_state.api_key = api_key
    
    st.markdown("---")
    
    # 出荷日時入力
    st.subheader("📅 出荷日")
    shipment_date = st.date_input(
        "出荷日を選択",
        value=datetime.strptime(st.session_state.shipment_date, '%Y-%m-%d').date(),
        help="出荷予定日を選択してください"
    )
    st.session_state.shipment_date = shipment_date.strftime('%Y-%m-%d')
    
    st.markdown("---")
    st.markdown("### 📋 使い方")
    st.markdown("""
    1. Gemini APIキーを入力
    2. 出荷日を選択
    3. 画像をアップロード or メールから取得
    4. 解析結果を確認・修正
    5. PDFを生成
    """)

# メインコンテンツ
if not api_key:
    st.warning("⚠️ サイドバーでGemini APIキーを入力してください。")
    st.stop()

# ===== タブ1: 画像解析 =====
with tab1:
    uploaded_file = st.file_uploader("注文画像をアップロード", type=['png', 'jpg', 'jpeg'])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="アップロード画像", use_container_width=True)
        
        # 新しい画像がアップロードされた場合はセッション状態をリセット
        if st.session_state.image_uploaded != uploaded_file.name:
            st.session_state.parsed_data = None
            st.session_state.labels = []
            st.session_state.image_uploaded = uploaded_file.name
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🔍 AI解析を実行", type="primary", use_container_width=True):
                with st.spinner('AIが解析中...'):
                    order_data = parse_order_image(image, api_key)
                    if order_data:
                        # 検証と修正
                        validated_data = validate_and_fix_order_data(order_data)
                        st.session_state.parsed_data = validated_data
                        st.session_state.labels = []
                        st.success(f"✅ {len(validated_data)}件のデータを読み取りました")
                        st.rerun()
                    else:
                        st.error("解析に失敗しました。画像を確認してください。")
        
        with col2:
            if st.button("🔄 解析結果をリセット", use_container_width=True):
                st.session_state.parsed_data = None
                st.session_state.labels = []
                st.rerun()

# ===== タブ2: メール自動読み取り =====
with tab2:
    st.subheader("📧 メール自動読み取り")
    st.write("メールから注文画像を自動取得して解析します。")
    
    # 保存された設定を読み込み
    saved_config = st.session_state.email_config
    
    # Streamlit Secretsから設定を読み込む（最優先）
    try:
        if hasattr(st, 'secrets'):
            try:
                secrets_email = st.secrets.get("email", {})
                if secrets_email and secrets_email.get("email_address"):
                    saved_config = {
                        "imap_server": secrets_email.get("imap_server", detect_imap_server(secrets_email.get("email_address", ""))),
                        "email_address": secrets_email.get("email_address", ""),
                        "sender_email": secrets_email.get("sender_email", ""),
                        "days_back": secrets_email.get("days_back", 1)
                    }
                    st.session_state.email_config = saved_config
                    st.info("💡 Streamlit Secretsから設定を読み込みました")
            except Exception:
                # secretsファイルが存在しない場合は無視
                pass
    except Exception:
        pass
    
    # メール設定
    with st.expander("📮 メール設定", expanded=False):
        # IMAPサーバー（自動判定）
        default_imap = saved_config.get("imap_server", "")
        if not default_imap and saved_config.get("email_address"):
            default_imap = detect_imap_server(saved_config.get("email_address", ""))
        if not default_imap:
            default_imap = "imap.gmail.com"
        
        imap_server = st.text_input(
            "IMAPサーバー", 
            value=default_imap, 
            help="例: imap.gmail.com, imap.outlook.com（メールアドレスから自動判定されます）"
        )
        
        # メールアドレス（入力時にIMAPサーバーを自動判定）
        email_address = st.text_input(
            "メールアドレス", 
            value=saved_config.get("email_address", ""),
            help="受信するメールアドレス（入力するとIMAPサーバーを自動判定します）",
            key="email_addr_input"
        )
        
        # メールアドレスが変更されたらIMAPサーバーを自動更新
        if email_address and "@" in email_address:
            auto_detected = detect_imap_server(email_address)
            if auto_detected != default_imap:
                if 'auto_imap_server' not in st.session_state or st.session_state.auto_imap_server != auto_detected:
                    st.session_state.auto_imap_server = auto_detected
                    st.info(f"💡 IMAPサーバーを自動判定: {auto_detected}")
                imap_server = auto_detected
        
        # パスワード（セッション状態に保存、ファイルには保存しない）
        email_password = st.text_input(
            "パスワード", 
            type="password", 
            value=st.session_state.email_password,
            help="メールパスワードまたはアプリパスワード（このセッション中のみ保存）",
            key="email_pass_input"
        )
        st.session_state.email_password = email_password
        
        # 送信者フィルタ
        sender_email = st.text_input(
            "送信者メール（フィルタ）", 
            value=saved_config.get("sender_email", ""),
            help="特定の送信者のみ取得する場合（空欄で全て）"
        )
        
        # 何日前まで遡るか
        days_back = st.number_input(
            "何日前まで遡るか", 
            min_value=1, 
            max_value=30, 
            value=saved_config.get("days_back", 1)
        )
        
        # 設定を保存するか（オプション）
        save_settings = st.checkbox(
            "設定を保存（メールアドレス、IMAPサーバー、送信者フィルタのみ。パスワードは保存されません）",
            value=False,
            help="チェックすると、次回起動時に設定が自動入力されます（パスワードは除く）"
        )
        
        if save_settings:
            save_email_config(imap_server, email_address, sender_email, days_back, save_to_file=True)
            st.session_state.email_config = {
                "imap_server": imap_server,
                "email_address": email_address,
                "sender_email": sender_email,
                "days_back": days_back
            }
            st.success("✅ 設定を保存しました（パスワードは保存されません）")
    
    # ワンクリックでメールチェック
    col1, col2 = st.columns([2, 1])
    
    with col1:
        if st.button("📬 メールをチェック", type="primary", use_container_width=True):
            if not email_address or not email_password:
                st.error("メールアドレスとパスワードを入力してください。")
            else:
                try:
                    with st.spinner('メールをチェック中...'):
                        results = check_email_for_orders(
                            imap_server=imap_server,
                            email_address=email_address,
                            password=email_password,
                            sender_email=sender_email if sender_email else None,
                            days_back=days_back
                        )
                    
                    if results:
                        st.success(f"✅ {len(results)}件のメールから画像を取得しました")
                        
                        for idx, result in enumerate(results):
                            with st.expander(f"📎 {result['filename']} - {result['subject']} ({result['date']})"):
                                st.image(result['image'], caption=result['filename'], use_container_width=True)
                                
                                if st.button(f"🔍 この画像を解析", key=f"parse_{idx}"):
                                    with st.spinner('解析中...'):
                                        order_data = parse_order_image(result['image'], api_key)
                                        if order_data:
                                            validated_data = validate_and_fix_order_data(order_data)
                                            st.session_state.parsed_data = validated_data
                                            st.session_state.labels = []
                                            st.success(f"✅ {len(validated_data)}件のデータを読み取りました")
                                            st.rerun()
                    else:
                        st.info("新しいメールは見つかりませんでした。")
                
                except Exception as e:
                    st.error(f"メールチェックエラー: {e}")
                    with st.expander("🔍 詳細なエラー情報"):
                        st.code(traceback.format_exc(), language="python")
                    st.info("💡 解決方法: IMAPサーバー設定、メールアドレス、パスワードを確認してください。Gmailの場合はアプリパスワードを使用してください。")
    
    with col2:
        # 設定をリセット
        if st.button("🔄 設定をリセット", use_container_width=True, help="入力内容をクリア"):
            st.session_state.email_password = ""
            st.rerun()
    
    # 設定が保存されている場合の表示
    if saved_config.get("email_address"):
        st.success(f"💾 設定が保存されています: **{saved_config.get('email_address')}** ({saved_config.get('imap_server', '自動判定')}) - パスワードのみ入力してください")

# ===== タブ3: 設定管理 =====
with tab3:
    st.subheader("⚙️ 設定管理")
    st.write("店舗名と品目名を動的に管理できます。")
    
    # 店舗名管理
    st.subheader("🏪 店舗名管理")
    stores = load_stores()
    
    col1, col2 = st.columns([3, 1])
    with col1:
        new_store = st.text_input("新しい店舗名を追加", placeholder="例: 新店舗", key="new_store_input")
    with col2:
        if st.button("追加", key="add_store"):
            if new_store and new_store.strip():
                if add_store(new_store.strip()):
                    st.success(f"✅ 「{new_store.strip()}」を追加しました")
                    st.rerun()
                else:
                    st.warning("既に存在する店舗名です")
    
    # 店舗名一覧（編集・削除可能）
    if stores:
        st.write("**登録済み店舗名:**")
        for store in stores:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"- {store}")
            with col2:
                if st.button("削除", key=f"del_store_{store}"):
                    if remove_store(store):
                        st.success(f"✅ 「{store}」を削除しました")
                        st.rerun()
    
    st.divider()
    
    # 品目名管理
    st.subheader("🥬 品目名管理")
    items = load_items()
    
    col1, col2 = st.columns([3, 1])
    with col1:
        new_item = st.text_input("新しい品目名を追加", placeholder="例: 新野菜", key="new_item_input")
    with col2:
        if st.button("追加", key="add_item"):
            if new_item and new_item.strip():
                if add_new_item(new_item.strip()):
                    st.success(f"✅ 「{new_item.strip()}」を追加しました")
                    st.rerun()
                else:
                    st.warning("既に存在する品目名です")
    
    # 品目名一覧（編集・削除可能）
    if items:
        st.write("**登録済み品目名:**")
        for normalized, variants in items.items():
            with st.expander(f"📦 {normalized} (バリアント: {', '.join(variants)})"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    new_variant = st.text_input(f"「{normalized}」の新しい表記を追加", key=f"variant_{normalized}", placeholder="例: 別表記")
                with col2:
                    if st.button("追加", key=f"add_variant_{normalized}"):
                        if new_variant and new_variant.strip():
                            add_item_variant(normalized, new_variant.strip())
                            st.success(f"✅ 「{new_variant.strip()}」を追加しました")
                            st.rerun()
                
                if st.button("削除", key=f"del_item_{normalized}"):
                    if remove_item(normalized):
                        st.success(f"✅ 「{normalized}」を削除しました")
                        st.rerun()

# ===== 共通: 解析結果の表示と編集 =====
if st.session_state.parsed_data:
    st.markdown("---")
    st.header("📊 解析結果の確認・編集")
    st.write("以下のテーブルでデータを確認・編集できます。編集後は「ラベルを生成」ボタンを押してください。")
    
    # 編集可能なデータフレーム
    df_data = []
    for entry in st.session_state.parsed_data:
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        total_quantity = (unit * boxes) + remainder
        
        df_data.append({
            '店舗名': entry.get('store', ''),
            '品目': entry.get('item', ''),
            '規格': entry.get('spec', ''),
            '入数(unit)': unit,
            '箱数(boxes)': boxes,
            '端数(remainder)': remainder,
            '合計数量': total_quantity
        })
    
    df = pd.DataFrame(df_data)
    
    # データエディタ
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            '店舗名': st.column_config.SelectboxColumn(
                '店舗名',
                help='店舗名を選択してください',
                options=get_known_stores(),
                required=True
            ),
            '品目': st.column_config.TextColumn('品目', required=True),
            '規格': st.column_config.TextColumn('規格'),
            '入数(unit)': st.column_config.NumberColumn('入数(unit)', min_value=0, step=1),
            '箱数(boxes)': st.column_config.NumberColumn('箱数(boxes)', min_value=0, step=1),
            '端数(remainder)': st.column_config.NumberColumn('端数(remainder)', min_value=0, step=1),
            '合計数量': st.column_config.NumberColumn('合計数量', disabled=True)
        }
    )
    
    # 編集後のデータを更新
    edited_df['合計数量'] = edited_df['入数(unit)'] * edited_df['箱数(boxes)'] + edited_df['端数(remainder)']
    
    # データが変更されたかチェック
    df_for_compare = df.drop(columns=['合計数量'])
    edited_df_for_compare = edited_df.drop(columns=['合計数量'])
    
    if not df_for_compare.equals(edited_df_for_compare):
        updated_data = []
        for _, row in edited_df.iterrows():
            # 品目名の正規化
            normalized_item = normalize_item_name(row['品目'])
            # 店舗名の検証
            validated_store = validate_store_name(row['店舗名']) or row['店舗名']
            
            # 規格の処理（NaNやNoneに対応）
            try:
                spec_value = row['規格']
                if pd.isna(spec_value) or spec_value is None:
                    spec_value = ''
                else:
                    spec_value = str(spec_value).strip()
            except (KeyError, TypeError):
                spec_value = ''
            
            updated_data.append({
                'store': validated_store,
                'item': normalized_item,
                'spec': spec_value,
                'unit': int(row['入数(unit)']),
                'boxes': int(row['箱数(boxes)']),
                'remainder': int(row['端数(remainder)'])
            })
        
        st.session_state.parsed_data = updated_data
        st.info("✅ データを更新しました。PDFを生成する場合は下のボタンを押してください。")
    
    st.divider()
    
    # ラベル生成
    if st.button("📋 ラベルを生成", type="primary", use_container_width=True, key="pdf_gen_tab1"):
        if st.session_state.parsed_data:
            try:
                # 最終的な検証
                final_data = validate_and_fix_order_data(st.session_state.parsed_data)
                
                labels = generate_labels_from_data(final_data, st.session_state.shipment_date)
                st.session_state.labels = labels
                
                if labels:
                    st.success(f"✅ {len(labels)}個のラベルを生成しました！")
                else:
                    st.error("❌ ラベルを生成できませんでした。数量を確認してください。")
            except Exception as e:
                st.error(f"❌ ラベル生成エラー: {e}")
                st.exception(e)

# ===== PDF生成 =====
if st.session_state.labels and st.session_state.parsed_data:
    st.markdown("---")
    st.header("📄 PDF生成")
    
    if st.button("🖨️ PDFを生成", type="primary", use_container_width=True, key="pdf_gen_main"):
        try:
            # 最終的な検証
            final_data = validate_and_fix_order_data(st.session_state.parsed_data)
            
            # 一時ファイルにPDFを生成
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                pdf_path = tmp_file.name
                
                # 出荷一覧表データを生成
                summary_data = generate_summary_table(final_data)
                
                generator = LabelPDFGenerator()
                generator.generate_pdf(
                    st.session_state.labels,
                    summary_data,
                    st.session_state.shipment_date,
                    pdf_path
                )
                
                # PDFファイルを読み込んでダウンロードボタンを表示
                with open(pdf_path, 'rb') as f:
                    pdf_bytes = f.read()
                
                st.download_button(
                    label="📥 PDFをダウンロード (一覧表付き)",
                    data=pdf_bytes,
                    file_name=f"出荷ラベル_{st.session_state.shipment_date.replace('-', '')}.pdf",
                    mime="application/pdf"
                )
                
                # 一時ファイルを削除
                try:
                    os.unlink(pdf_path)
                except (PermissionError, OSError):
                    pass
                
                st.success("✅ PDFが生成されました！")
            
            # LINE用集計の表示
            st.subheader("📋 LINE用集計（コピー用）")
            line_text = generate_line_summary(final_data)
            st.code(line_text, language="text")
            st.write("↑ タップしてコピーし、LINEに貼り付けてください。")
        
        except Exception as e:
            st.error(f"❌ PDF生成エラーが発生しました")
            st.error(f"エラー詳細: {str(e)}")
            with st.expander("🔍 詳細なエラー情報（開発者用）"):
                st.code(traceback.format_exc(), language="python")
            st.info("💡 解決方法: データを確認し、数値が正しく入力されているか確認してください。")

# フッター
st.markdown("---")
st.markdown("### 📝 注意事項")
st.markdown("""
- 店舗ごとにすべてのラベルが印刷されます（複数ページ対応）
- 端数箱（最後の1箱）は太い破線枠で囲まれ、数量が大きく表示されます
- 切断用のガイド線は薄いグレーの破線で表示されます
- PDFの最初のページに出荷一覧表が含まれます
- 新しい店舗名・品目名は自動学習されます
""")
=======
"""
出荷ラベル生成Streamlitアプリ
FAX注文書画像をアップロードして、店舗ごとの出荷ラベルPDFを生成
"""
import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
from pdf_generator import LabelPDFGenerator
import tempfile
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
import re
import traceback

# 設定管理モジュールのインポート
from config_manager import (
    load_stores, save_stores, add_store, remove_store,
    load_items, save_items, add_item_variant, add_new_item, remove_item,
    auto_learn_store, auto_learn_item
)
from email_config_manager import load_email_config, save_email_config, detect_imap_server
from email_reader import check_email_for_orders

# ページ設定
st.set_page_config(
    page_title="出荷ラベル生成アプリ",
    page_icon="📦",
    layout="wide"
)

# セッション状態の初期化
if 'api_key' not in st.session_state:
    st.session_state.api_key = ''
if 'parsed_data' not in st.session_state:
    st.session_state.parsed_data = None
if 'labels' not in st.session_state:
    st.session_state.labels = []
if 'shipment_date' not in st.session_state:
    st.session_state.shipment_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
if 'image_uploaded' not in st.session_state:
    st.session_state.image_uploaded = None
if 'email_config' not in st.session_state:
    # st.secretsに安全にアクセス（secretsファイルが存在しない場合でもエラーにならないように）
    try:
        secrets_obj = st.secrets if hasattr(st, 'secrets') else None
    except Exception:
        secrets_obj = None
    st.session_state.email_config = load_email_config(secrets_obj)
if 'email_password' not in st.session_state:
    st.session_state.email_password = ""


def safe_int(v):
    """安全に整数に変換"""
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    s = re.sub(r'\D', '', str(v))
    return int(s) if s else 0


def get_known_stores():
    """店舗名リストを取得（動的）"""
    return load_stores()


def get_item_normalization():
    """品目名正規化マップを取得（動的）"""
    return load_items()


def normalize_item_name(item_name, auto_learn=True):
    """品目名を正規化する（動的設定対応）"""
    if not item_name:
        return ""
    item_name = str(item_name).strip()
    item_normalization = get_item_normalization()
    
    for normalized, variants in item_normalization.items():
        if item_name in variants or any(variant in item_name for variant in variants):
            return normalized
    
    # 見つからない場合、自動学習
    if auto_learn:
        return auto_learn_item(item_name)
    return item_name


def validate_store_name(store_name, auto_learn=True):
    """店舗名を検証し、最も近い店舗名を返す（動的設定対応）"""
    if not store_name:
        return None
    store_name = str(store_name).strip()
    known_stores = get_known_stores()
    
    # 完全一致
    if store_name in known_stores:
        return store_name
    # 部分一致
    for known_store in known_stores:
        if known_store in store_name or store_name in known_store:
            return known_store
    
    # 見つからない場合、自動学習
    if auto_learn:
        return auto_learn_store(store_name)
    return None


def parse_order_image(image: Image.Image, api_key: str) -> list:
    """
    Gemini APIで注文書画像を解析（複数店舗対応）
    
    Args:
        image: PIL Imageオブジェクト
        api_key: Gemini APIキー
    
    Returns:
        解析結果のリスト [{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}]
    """
    genai.configure(api_key=api_key)
    
    # モデルを初期化（gemini-2.0-flashを優先）
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
    except:
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
        except:
            try:
                model = genai.GenerativeModel('gemini-1.5-pro')
            except:
                model = genai.GenerativeModel('gemini-pro-vision')
    
    # 店舗名・品目名リストを取得
    known_stores = get_known_stores()
    item_normalization = get_item_normalization()
    store_list = "、".join(known_stores)
    item_list = ", ".join(item_normalization.keys())
    
    # プロンプト（既存リポジトリを参考）
    prompt = f"""
画像を解析し、以下の厳密なルールに従ってJSONで返してください。

【店舗名リスト（参考）】
{store_list}
※上記リストにない店舗名も読み取ってください。

【品目名の正規化ルール】
{json.dumps(item_normalization, ensure_ascii=False, indent=2)}

【重要ルール】
1. 店舗名の後に「:」または改行がある場合、その後の行は全てその店舗の注文です
2. 品目名がない行（例：「50×1」）は、直前の品目の続きとして処理してください
3. 「/」で区切られた複数の注文は、同じ店舗・同じ品目として統合してください
   - 例：「胡瓜バラ100×7 / 50×1」→ 胡瓜バラ100本×7箱 + 端数50本
4. 「胡瓜バラ」と「胡瓜3本」は別の規格として扱ってください
5. unit, boxes, remainderには「数字のみ」を入れてください

【計算ルール】
- 胡瓜(3本P): 30本/箱 → unit=30
- 胡瓜(バラ): 100本/箱（50本以上なら50本箱1、未満はバラ）→ unit=100
- 春菊: 30袋/箱 → unit=30
- 青梗菜: 20袋/箱 → unit=20
- 長ネギ(2本P): 30本/箱 → unit=30

【最重要：総数（パック数）の表記について】
- 「×数字」の表記（例：「×180」「×100」「×50」）は「総数（パック数）」を意味します
- 「×数字」は「箱数」ではなく「総数」です！絶対に間違えないでください！
- この場合、unit（1箱あたりの入数）とboxes（箱数）を逆算してください
- 計算式：総数 = unit × boxes + remainder
- 総数がunitで割り切れる場合：boxes = 総数 ÷ unit, remainder = 0
- 総数がunitで割り切れない場合：boxes = 総数 ÷ unit（切り捨て）, remainder = 総数 - (unit × boxes)

【数量計算の例（重要：×数字は総数を意味する）】
- 「胡瓜3本×180」→ 総数180パック = unit=30の場合、boxes=6, remainder=0 (180÷30=6箱)
- 「胡瓜3本×100」→ 総数100パック = unit=30の場合、boxes=3, remainder=10 (100÷30=3箱余り10)
- 「胡瓜3本×60」→ 総数60パック = unit=30の場合、boxes=2, remainder=0 (60÷30=2箱)
- 「胡瓜3本×30」→ 総数30パック = unit=30の場合、boxes=1, remainder=0 (30÷30=1箱)
- 「胡瓜3本×20」→ 総数20パック = unit=30の場合、boxes=0, remainder=20 (20<30なので端数のみ)
- 「春菊×50」→ 総数50パック = unit=30の場合、boxes=1, remainder=20 (50÷30=1箱余り20)
- 「ネギ2本×80」→ 総数80パック = unit=30の場合、boxes=2, remainder=20 (80÷30=2箱余り20)
- 「胡瓜バラ100×7 / 50×1」→ これは特殊な表記：100本/箱×7箱 + 端数50本 = unit=100, boxes=7, remainder=50

【出力JSON形式】
[{{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}}]

必ず全ての店舗と品目を漏れなく読み取ってください。
"""
    
    try:
        response = model.generate_content([prompt, image])
        # レスポンスからJSONを抽出
        text = response.text.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            parts = text.split('```')
            for part in parts:
                if '{' in part and '[' in part:
                    text = part.strip()
                    break
        
        # JSONをパース
        result = json.loads(text)
        # リストでない場合はリストに変換
        if isinstance(result, dict):
            result = [result]
        return result
    except json.JSONDecodeError as e:
        st.error(f"JSON解析エラー: {e}")
        st.text(f"レスポンス内容: {text[:500]}")
        return None
    except Exception as e:
        st.error(f"画像解析エラー: {e}")
        return None


def validate_and_fix_order_data(order_data, auto_learn=True):
    """AIが読み取ったデータを検証し、必要に応じて修正する（自動学習対応）"""
    if not order_data:
        return []
    
    validated_data = []
    errors = []
    learned_stores = []
    learned_items = []
    
    known_stores = get_known_stores()
    
    for i, entry in enumerate(order_data):
        # 必須フィールドのチェック
        store = entry.get('store', '').strip()
        item = entry.get('item', '').strip()
        
        # 店舗名の検証と修正（自動学習）
        validated_store = validate_store_name(store, auto_learn=auto_learn)
        if not validated_store and store:
            if auto_learn:
                validated_store = auto_learn_store(store)
                if validated_store not in learned_stores:
                    learned_stores.append(validated_store)
            else:
                errors.append(f"行{i+1}: 不明な店舗名「{store}」")
                # 最も近い店舗名を推測
                for known_store in known_stores:
                    if any(char in store for char in known_store):
                        validated_store = known_store
                        break
        
        # 品目名の正規化（自動学習）
        normalized_item = normalize_item_name(item, auto_learn=auto_learn)
        if not normalized_item and item:
            if auto_learn:
                normalized_item = auto_learn_item(item)
                if normalized_item not in learned_items:
                    learned_items.append(normalized_item)
            else:
                errors.append(f"行{i+1}: 品目名「{item}」を正規化できませんでした")
        
        # 数量の検証
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        
        # 数量が0の場合は警告
        if unit == 0 and boxes == 0 and remainder == 0:
            errors.append(f"行{i+1}: 数量が全て0です（店舗: {store}, 品目: {item}）")
        
        # 検証済みデータを追加
        spec_value = entry.get('spec', '')
        if spec_value is None:
            spec_value = ''
        else:
            spec_value = str(spec_value).strip()
        
        validated_entry = {
            'store': validated_store or store,
            'item': normalized_item or item,
            'spec': spec_value,
            'unit': unit,
            'boxes': boxes,
            'remainder': remainder
        }
        validated_data.append(validated_entry)
    
    # 自動学習の結果を表示
    if auto_learn:
        if learned_stores:
            st.success(f"✨ 新しい店舗名を学習しました: {', '.join(learned_stores)}")
        if learned_items:
            st.success(f"✨ 新しい品目名を学習しました: {', '.join(learned_items)}")
    
    # エラーがある場合は表示
    if errors:
        st.warning("⚠️ 検証で以下の問題が見つかりました:")
        for error in errors:
            st.write(f"- {error}")
    
    return validated_data


def generate_labels_from_data(order_data: list, shipment_date: str) -> list:
    """
    解析データからラベルリストを生成（店舗ごと）
    
    Args:
        order_data: 解析結果のリスト [{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}]
        shipment_date: 出荷日（YYYY-MM-DD形式）
    
    Returns:
        ラベル情報のリスト
    """
    labels = []
    shipment_date_display = datetime.strptime(shipment_date, '%Y-%m-%d').strftime('%m月%d日')
    
    for entry in order_data:
        store = entry.get('store', '')
        item = entry.get('item', '')
        spec = entry.get('spec', '')
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        
        if unit == 0:
            continue
        
        # 単位を判定（パックと袋は「袋」に統一）
        unit_label = '本'
        if '春菊' in item or '青梗菜' in item or 'チンゲン菜' in item:
            unit_label = '袋'
        elif 'ネギ' in item or 'ねぎ' in item:
            unit_label = '袋'  # パックから袋に統一
        elif '胡瓜' in item or 'きゅうり' in item:
            if 'バラ' in spec or 'ばら' in spec:
                unit_label = '本'
            else:
                unit_label = '袋'  # パックから袋に統一
        
        # 通常箱のラベル
        total_boxes = boxes + (1 if remainder > 0 else 0)
        for i in range(boxes):
            labels.append({
                'store': store,
                'item': item,
                'spec': spec,
                'quantity': f"{unit}{unit_label}",
                'sequence': f"{i+1}/{total_boxes}",
                'is_fraction': False,
                'shipment_date': shipment_date_display,
                'unit': unit,
                'boxes': boxes,
                'remainder': remainder
            })
        
        # 端数箱のラベル（余りがある場合）
        if remainder > 0:
            labels.append({
                'store': store,
                'item': item,
                'spec': spec,
                'quantity': f"{remainder}{unit_label}",
                'sequence': f"{total_boxes}/{total_boxes}",
                'is_fraction': True,
                'shipment_date': shipment_date_display,
                'unit': unit,
                'boxes': boxes,
                'remainder': remainder
            })
    
    return labels


def get_unit_label_for_item(item: str, spec: str) -> str:
    """
    品目名と規格から単位を判定
    
    Args:
        item: 品目名
        spec: 規格
    
    Returns:
        単位（'本'、'袋'など）
    """
    item_lower = item.lower() if item else ""
    spec_lower = spec.lower() if spec else ""
    
    # 単位を判定（パックと袋は「袋」に統一）
    unit_label = '本'
    if '春菊' in item or '青梗菜' in item or 'チンゲン菜' in item:
        unit_label = '袋'
    elif 'ネギ' in item or 'ねぎ' in item:
        unit_label = '袋'  # パックから袋に統一
    elif '胡瓜' in item or 'きゅうり' in item:
        if 'バラ' in spec or 'ばら' in spec_lower:
            unit_label = '本'
        else:
            unit_label = '袋'  # パックから袋に統一
    
    return unit_label


def generate_summary_table(order_data: list) -> list:
    """
    出荷一覧表用のデータを生成
    
    Args:
        order_data: 解析結果のリスト
    
    Returns:
        一覧表用のデータリスト
    """
    summary = []
    for entry in order_data:
        store = entry.get('store', '')
        item = entry.get('item', '')
        spec = entry.get('spec', '')
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        unit = safe_int(entry.get('unit', 0))
        
        rem_box = 1 if remainder > 0 else 0
        total_packs = boxes + rem_box  # フル箱 + 端数箱 = パック数
        total_quantity = (unit * boxes) + remainder  # 総数量
        
        # 単位を判定
        unit_label = get_unit_label_for_item(item, spec)
        
        summary.append({
            'store': store,
            'item': item,
            'spec': spec,
            'boxes': boxes,
            'rem_box': rem_box,
            'total_packs': total_packs,
            'total_quantity': total_quantity,
            'unit': unit,
            'unit_label': unit_label  # 単位情報を追加
        })
    
    return summary


def generate_line_summary(order_data: list) -> str:
    """
    LINEに貼り付け可能な集計テキストを生成
    
    Args:
        order_data: 解析結果のリスト
    
    Returns:
        LINE用の集計テキスト
    """
    summary_packs = defaultdict(int)
    
    for entry in order_data:
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        total = (unit * boxes) + remainder
        
        # キーをitemとspecの組み合わせにする（胡瓜の3本Pとバラを別物として扱う）
        item = entry.get('item', '不明')
        spec = entry.get('spec', '').strip()
        key = (item, spec)  # タプルをキーとして使用
        summary_packs[key] += total
    
    # 単位判定関数（共通関数を使用）
    
    line_text = f"【{datetime.now().strftime('%m/%d')} 出荷・作成総数】\n"
    # キーをソートして表示（品目名→規格の順）
    sorted_items = sorted(summary_packs.items(), key=lambda x: (x[0][0], x[0][1]))
    for (item, spec), total in sorted_items:
        unit_label = get_unit_label_for_item(item, spec)
        # 表示形式: 品目名(規格)：数量単位
        if spec:
            display_name = f"{item}({spec})"
        else:
            display_name = item
        line_text += f"・{display_name}：{total}{unit_label}\n"
    
    return line_text


# メインUI
st.title("📦 出荷ラベル生成アプリ")
st.markdown("FAX注文書画像をアップロードして、店舗ごとの出荷ラベルPDFを生成します。")

# タブ作成
tab1, tab2, tab3 = st.tabs(["📸 画像解析", "📧 メール自動読み取り", "⚙️ 設定管理"])

# サイドバー
with st.sidebar:
    st.header("⚙️ 設定")
    
    api_key = st.text_input(
        "Gemini APIキー",
        value=st.session_state.api_key,
        type="password",
        help="Google Gemini APIのキーを入力してください"
    )
    st.session_state.api_key = api_key
    
    st.markdown("---")
    
    # 出荷日時入力
    st.subheader("📅 出荷日")
    shipment_date = st.date_input(
        "出荷日を選択",
        value=datetime.strptime(st.session_state.shipment_date, '%Y-%m-%d').date(),
        help="出荷予定日を選択してください"
    )
    st.session_state.shipment_date = shipment_date.strftime('%Y-%m-%d')
    
    st.markdown("---")
    st.markdown("### 📋 使い方")
    st.markdown("""
    1. Gemini APIキーを入力
    2. 出荷日を選択
    3. 画像をアップロード or メールから取得
    4. 解析結果を確認・修正
    5. PDFを生成
    """)

# メインコンテンツ
if not api_key:
    st.warning("⚠️ サイドバーでGemini APIキーを入力してください。")
    st.stop()

# ===== タブ1: 画像解析 =====
with tab1:
    uploaded_file = st.file_uploader("注文画像をアップロード", type=['png', 'jpg', 'jpeg'])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="アップロード画像", use_container_width=True)
        
        # 新しい画像がアップロードされた場合はセッション状態をリセット
        if st.session_state.image_uploaded != uploaded_file.name:
            st.session_state.parsed_data = None
            st.session_state.labels = []
            st.session_state.image_uploaded = uploaded_file.name
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🔍 AI解析を実行", type="primary", use_container_width=True):
                with st.spinner('AIが解析中...'):
                    order_data = parse_order_image(image, api_key)
                    if order_data:
                        # 検証と修正
                        validated_data = validate_and_fix_order_data(order_data)
                        st.session_state.parsed_data = validated_data
                        st.session_state.labels = []
                        st.success(f"✅ {len(validated_data)}件のデータを読み取りました")
                        st.rerun()
                    else:
                        st.error("解析に失敗しました。画像を確認してください。")
        
        with col2:
            if st.button("🔄 解析結果をリセット", use_container_width=True):
                st.session_state.parsed_data = None
                st.session_state.labels = []
                st.rerun()

# ===== タブ2: メール自動読み取り =====
with tab2:
    st.subheader("📧 メール自動読み取り")
    st.write("メールから注文画像を自動取得して解析します。")
    
    # 保存された設定を読み込み
    saved_config = st.session_state.email_config
    
    # Streamlit Secretsから設定を読み込む（最優先）
    try:
        if hasattr(st, 'secrets'):
            try:
                secrets_email = st.secrets.get("email", {})
                if secrets_email and secrets_email.get("email_address"):
                    saved_config = {
                        "imap_server": secrets_email.get("imap_server", detect_imap_server(secrets_email.get("email_address", ""))),
                        "email_address": secrets_email.get("email_address", ""),
                        "sender_email": secrets_email.get("sender_email", ""),
                        "days_back": secrets_email.get("days_back", 1)
                    }
                    st.session_state.email_config = saved_config
                    st.info("💡 Streamlit Secretsから設定を読み込みました")
            except Exception:
                # secretsファイルが存在しない場合は無視
                pass
    except Exception:
        pass
    
    # メール設定
    with st.expander("📮 メール設定", expanded=False):
        # IMAPサーバー（自動判定）
        default_imap = saved_config.get("imap_server", "")
        if not default_imap and saved_config.get("email_address"):
            default_imap = detect_imap_server(saved_config.get("email_address", ""))
        if not default_imap:
            default_imap = "imap.gmail.com"
        
        imap_server = st.text_input(
            "IMAPサーバー", 
            value=default_imap, 
            help="例: imap.gmail.com, imap.outlook.com（メールアドレスから自動判定されます）"
        )
        
        # メールアドレス（入力時にIMAPサーバーを自動判定）
        email_address = st.text_input(
            "メールアドレス", 
            value=saved_config.get("email_address", ""),
            help="受信するメールアドレス（入力するとIMAPサーバーを自動判定します）",
            key="email_addr_input"
        )
        
        # メールアドレスが変更されたらIMAPサーバーを自動更新
        if email_address and "@" in email_address:
            auto_detected = detect_imap_server(email_address)
            if auto_detected != default_imap:
                if 'auto_imap_server' not in st.session_state or st.session_state.auto_imap_server != auto_detected:
                    st.session_state.auto_imap_server = auto_detected
                    st.info(f"💡 IMAPサーバーを自動判定: {auto_detected}")
                imap_server = auto_detected
        
        # パスワード（セッション状態に保存、ファイルには保存しない）
        email_password = st.text_input(
            "パスワード", 
            type="password", 
            value=st.session_state.email_password,
            help="メールパスワードまたはアプリパスワード（このセッション中のみ保存）",
            key="email_pass_input"
        )
        st.session_state.email_password = email_password
        
        # 送信者フィルタ
        sender_email = st.text_input(
            "送信者メール（フィルタ）", 
            value=saved_config.get("sender_email", ""),
            help="特定の送信者のみ取得する場合（空欄で全て）"
        )
        
        # 何日前まで遡るか
        days_back = st.number_input(
            "何日前まで遡るか", 
            min_value=1, 
            max_value=30, 
            value=saved_config.get("days_back", 1)
        )
        
        # 設定を保存するか（オプション）
        save_settings = st.checkbox(
            "設定を保存（メールアドレス、IMAPサーバー、送信者フィルタのみ。パスワードは保存されません）",
            value=False,
            help="チェックすると、次回起動時に設定が自動入力されます（パスワードは除く）"
        )
        
        if save_settings:
            save_email_config(imap_server, email_address, sender_email, days_back, save_to_file=True)
            st.session_state.email_config = {
                "imap_server": imap_server,
                "email_address": email_address,
                "sender_email": sender_email,
                "days_back": days_back
            }
            st.success("✅ 設定を保存しました（パスワードは保存されません）")
    
    # ワンクリックでメールチェック
    col1, col2 = st.columns([2, 1])
    
    with col1:
        if st.button("📬 メールをチェック", type="primary", use_container_width=True):
            if not email_address or not email_password:
                st.error("メールアドレスとパスワードを入力してください。")
            else:
                try:
                    with st.spinner('メールをチェック中...'):
                        results = check_email_for_orders(
                            imap_server=imap_server,
                            email_address=email_address,
                            password=email_password,
                            sender_email=sender_email if sender_email else None,
                            days_back=days_back
                        )
                    
                    if results:
                        st.success(f"✅ {len(results)}件のメールから画像を取得しました")
                        
                        for idx, result in enumerate(results):
                            with st.expander(f"📎 {result['filename']} - {result['subject']} ({result['date']})"):
                                st.image(result['image'], caption=result['filename'], use_container_width=True)
                                
                                if st.button(f"🔍 この画像を解析", key=f"parse_{idx}"):
                                    with st.spinner('解析中...'):
                                        order_data = parse_order_image(result['image'], api_key)
                                        if order_data:
                                            validated_data = validate_and_fix_order_data(order_data)
                                            st.session_state.parsed_data = validated_data
                                            st.session_state.labels = []
                                            st.success(f"✅ {len(validated_data)}件のデータを読み取りました")
                                            st.rerun()
                    else:
                        st.info("新しいメールは見つかりませんでした。")
                
                except Exception as e:
                    st.error(f"メールチェックエラー: {e}")
                    with st.expander("🔍 詳細なエラー情報"):
                        st.code(traceback.format_exc(), language="python")
                    st.info("💡 解決方法: IMAPサーバー設定、メールアドレス、パスワードを確認してください。Gmailの場合はアプリパスワードを使用してください。")
    
    with col2:
        # 設定をリセット
        if st.button("🔄 設定をリセット", use_container_width=True, help="入力内容をクリア"):
            st.session_state.email_password = ""
            st.rerun()
    
    # 設定が保存されている場合の表示
    if saved_config.get("email_address"):
        st.success(f"💾 設定が保存されています: **{saved_config.get('email_address')}** ({saved_config.get('imap_server', '自動判定')}) - パスワードのみ入力してください")

# ===== タブ3: 設定管理 =====
with tab3:
    st.subheader("⚙️ 設定管理")
    st.write("店舗名と品目名を動的に管理できます。")
    
    # 店舗名管理
    st.subheader("🏪 店舗名管理")
    stores = load_stores()
    
    col1, col2 = st.columns([3, 1])
    with col1:
        new_store = st.text_input("新しい店舗名を追加", placeholder="例: 新店舗", key="new_store_input")
    with col2:
        if st.button("追加", key="add_store"):
            if new_store and new_store.strip():
                if add_store(new_store.strip()):
                    st.success(f"✅ 「{new_store.strip()}」を追加しました")
                    st.rerun()
                else:
                    st.warning("既に存在する店舗名です")
    
    # 店舗名一覧（編集・削除可能）
    if stores:
        st.write("**登録済み店舗名:**")
        for store in stores:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"- {store}")
            with col2:
                if st.button("削除", key=f"del_store_{store}"):
                    if remove_store(store):
                        st.success(f"✅ 「{store}」を削除しました")
                        st.rerun()
    
    st.divider()
    
    # 品目名管理
    st.subheader("🥬 品目名管理")
    items = load_items()
    
    col1, col2 = st.columns([3, 1])
    with col1:
        new_item = st.text_input("新しい品目名を追加", placeholder="例: 新野菜", key="new_item_input")
    with col2:
        if st.button("追加", key="add_item"):
            if new_item and new_item.strip():
                if add_new_item(new_item.strip()):
                    st.success(f"✅ 「{new_item.strip()}」を追加しました")
                    st.rerun()
                else:
                    st.warning("既に存在する品目名です")
    
    # 品目名一覧（編集・削除可能）
    if items:
        st.write("**登録済み品目名:**")
        for normalized, variants in items.items():
            with st.expander(f"📦 {normalized} (バリアント: {', '.join(variants)})"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    new_variant = st.text_input(f"「{normalized}」の新しい表記を追加", key=f"variant_{normalized}", placeholder="例: 別表記")
                with col2:
                    if st.button("追加", key=f"add_variant_{normalized}"):
                        if new_variant and new_variant.strip():
                            add_item_variant(normalized, new_variant.strip())
                            st.success(f"✅ 「{new_variant.strip()}」を追加しました")
                            st.rerun()
                
                if st.button("削除", key=f"del_item_{normalized}"):
                    if remove_item(normalized):
                        st.success(f"✅ 「{normalized}」を削除しました")
                        st.rerun()

# ===== 共通: 解析結果の表示と編集 =====
if st.session_state.parsed_data:
    st.markdown("---")
    st.header("📊 解析結果の確認・編集")
    st.write("以下のテーブルでデータを確認・編集できます。編集後は「ラベルを生成」ボタンを押してください。")
    
    # 編集可能なデータフレーム
    df_data = []
    for entry in st.session_state.parsed_data:
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        total_quantity = (unit * boxes) + remainder
        
        df_data.append({
            '店舗名': entry.get('store', ''),
            '品目': entry.get('item', ''),
            '規格': entry.get('spec', ''),
            '入数(unit)': unit,
            '箱数(boxes)': boxes,
            '端数(remainder)': remainder,
            '合計数量': total_quantity
        })
    
    df = pd.DataFrame(df_data)
    
    # データエディタ
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            '店舗名': st.column_config.SelectboxColumn(
                '店舗名',
                help='店舗名を選択してください',
                options=get_known_stores(),
                required=True
            ),
            '品目': st.column_config.TextColumn('品目', required=True),
            '規格': st.column_config.TextColumn('規格'),
            '入数(unit)': st.column_config.NumberColumn('入数(unit)', min_value=0, step=1),
            '箱数(boxes)': st.column_config.NumberColumn('箱数(boxes)', min_value=0, step=1),
            '端数(remainder)': st.column_config.NumberColumn('端数(remainder)', min_value=0, step=1),
            '合計数量': st.column_config.NumberColumn('合計数量', disabled=True)
        }
    )
    
    # 編集後のデータを更新
    edited_df['合計数量'] = edited_df['入数(unit)'] * edited_df['箱数(boxes)'] + edited_df['端数(remainder)']
    
    # データが変更されたかチェック
    df_for_compare = df.drop(columns=['合計数量'])
    edited_df_for_compare = edited_df.drop(columns=['合計数量'])
    
    if not df_for_compare.equals(edited_df_for_compare):
        updated_data = []
        for _, row in edited_df.iterrows():
            # 品目名の正規化
            normalized_item = normalize_item_name(row['品目'])
            # 店舗名の検証
            validated_store = validate_store_name(row['店舗名']) or row['店舗名']
            
            # 規格の処理（NaNやNoneに対応）
            try:
                spec_value = row['規格']
                if pd.isna(spec_value) or spec_value is None:
                    spec_value = ''
                else:
                    spec_value = str(spec_value).strip()
            except (KeyError, TypeError):
                spec_value = ''
            
            updated_data.append({
                'store': validated_store,
                'item': normalized_item,
                'spec': spec_value,
                'unit': int(row['入数(unit)']),
                'boxes': int(row['箱数(boxes)']),
                'remainder': int(row['端数(remainder)'])
            })
        
        st.session_state.parsed_data = updated_data
        st.info("✅ データを更新しました。PDFを生成する場合は下のボタンを押してください。")
    
    st.divider()
    
    # ラベル生成
    if st.button("📋 ラベルを生成", type="primary", use_container_width=True, key="pdf_gen_tab1"):
        if st.session_state.parsed_data:
            try:
                # 最終的な検証
                final_data = validate_and_fix_order_data(st.session_state.parsed_data)
                
                labels = generate_labels_from_data(final_data, st.session_state.shipment_date)
                st.session_state.labels = labels
                
                if labels:
                    st.success(f"✅ {len(labels)}個のラベルを生成しました！")
                else:
                    st.error("❌ ラベルを生成できませんでした。数量を確認してください。")
            except Exception as e:
                st.error(f"❌ ラベル生成エラー: {e}")
                st.exception(e)

# ===== PDF生成 =====
if st.session_state.labels and st.session_state.parsed_data:
    st.markdown("---")
    st.header("📄 PDF生成")
    
    if st.button("🖨️ PDFを生成", type="primary", use_container_width=True, key="pdf_gen_main"):
        try:
            # 最終的な検証
            final_data = validate_and_fix_order_data(st.session_state.parsed_data)
            
            # 一時ファイルにPDFを生成
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                pdf_path = tmp_file.name
                
                # 出荷一覧表データを生成
                summary_data = generate_summary_table(final_data)
                
                generator = LabelPDFGenerator()
                generator.generate_pdf(
                    st.session_state.labels,
                    summary_data,
                    st.session_state.shipment_date,
                    pdf_path
                )
                
                # PDFファイルを読み込んでダウンロードボタンを表示
                with open(pdf_path, 'rb') as f:
                    pdf_bytes = f.read()
                
                st.download_button(
                    label="📥 PDFをダウンロード (一覧表付き)",
                    data=pdf_bytes,
                    file_name=f"出荷ラベル_{st.session_state.shipment_date.replace('-', '')}.pdf",
                    mime="application/pdf"
                )
                
                # 一時ファイルを削除
                try:
                    os.unlink(pdf_path)
                except (PermissionError, OSError):
                    pass
                
                st.success("✅ PDFが生成されました！")
            
            # LINE用集計の表示
            st.subheader("📋 LINE用集計（コピー用）")
            line_text = generate_line_summary(final_data)
            st.code(line_text, language="text")
            st.write("↑ タップしてコピーし、LINEに貼り付けてください。")
        
        except Exception as e:
            st.error(f"❌ PDF生成エラーが発生しました")
            st.error(f"エラー詳細: {str(e)}")
            with st.expander("🔍 詳細なエラー情報（開発者用）"):
                st.code(traceback.format_exc(), language="python")
            st.info("💡 解決方法: データを確認し、数値が正しく入力されているか確認してください。")

# フッター
st.markdown("---")
st.markdown("### 📝 注意事項")
st.markdown("""
- 店舗ごとにすべてのラベルが印刷されます（複数ページ対応）
- 端数箱（最後の1箱）は太い破線枠で囲まれ、数量が大きく表示されます
- 切断用のガイド線は薄いグレーの破線で表示されます
- PDFの最初のページに出荷一覧表が含まれます
- 新しい店舗名・品目名は自動学習されます
""")
>>>>>>> 8653932 (Initial commit)
