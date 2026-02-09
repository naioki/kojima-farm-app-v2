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
    auto_learn_store, auto_learn_item,
    load_units, lookup_unit, add_unit_if_new, set_unit, initialize_default_units,
    load_item_settings, save_item_settings, get_item_setting, set_item_setting, set_item_receive_as_boxes, remove_item_setting,
    DEFAULT_ITEM_SETTINGS, get_box_count_items
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
    # Streamlit SecretsからAPIキーを読み取る（優先）
    try:
        if hasattr(st, 'secrets'):
            try:
                st.session_state.api_key = st.secrets.get('GEMINI_API_KEY', '')
            except Exception:
                st.session_state.api_key = ''
        else:
            st.session_state.api_key = ''
    except Exception:
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

# デフォルト入数の初期化（初回起動時のみ）
if 'default_units_initialized' not in st.session_state:
    initialize_default_units()
    # 品目設定のデフォルト値も初期化
    item_settings = load_item_settings()
    # 長ねぎ・長ねぎバラが確実に50本に設定されているか確認
    for key in ["長ネギ", "長ねぎバラ", "長ネギバラ"]:
        if key in item_settings:
            if item_settings[key].get("default_unit") != 50 or item_settings[key].get("unit_type") != "本":
                set_item_setting(key, 50, "本")
    if not item_settings:
        # デフォルト設定を保存
        save_item_settings(DEFAULT_ITEM_SETTINGS)
    st.session_state.default_units_initialized = True


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
    
    # モデルを初期化（gemini-2.5-flash を優先、利用不可時は 2.0-flash 等にフォールバック）
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
    except Exception:
        try:
            model = genai.GenerativeModel('gemini-2.0-flash')
        except Exception:
            try:
                model = genai.GenerativeModel('gemini-1.5-flash')
            except Exception:
                try:
                    model = genai.GenerativeModel('gemini-1.5-pro')
                except Exception:
                    model = genai.GenerativeModel('gemini-pro-vision')
    
    # 店舗名・品目名リストを取得
    known_stores = get_known_stores()
    item_normalization = get_item_normalization()
    store_list = "、".join(known_stores)
    item_list = ", ".join(item_normalization.keys())
    # マスターデータを参照（品目名管理で設定した入数・箱数/総数）
    item_settings_for_prompt = load_item_settings()
    box_count_items = get_box_count_items()
    unit_lines = "\n".join([f"- {name}: {s.get('default_unit', 0)}{s.get('unit_type', '袋')}/コンテナ" for name, s in sorted(item_settings_for_prompt.items()) if s.get("default_unit", 0) > 0])
    box_count_str = "、".join(box_count_items) if box_count_items else "（なし）"
    
    # プロンプト（マスターデータを参照して計算）
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

【計算ルール（事前登録マスターデータ＝1コンテナあたりの入数）】
メールで送られてくるのは基本的に「総数」です。以下の登録入数を参照して、総数から箱数・端数を逆算してください。
{unit_lines}

【最重要：総数 vs 箱数】
- 「×数字」が総数の品目：boxes = 総数÷unit（切り捨て）, remainder = 総数 - unit×boxes で逆算してください。
- 「×数字」が箱数の品目（以下のみ）：{box_count_str} → ×数字をそのままboxesにし、unitは上記の値、remainder=0 で出力してください。

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

        # 入数が0の場合、入数マスターから補完（柔軟に変えられる仕組み）
        if unit <= 0:
            spec_for_lookup = (entry.get('spec') or '').strip() if entry.get('spec') is not None else ''
            looked_up = lookup_unit(normalized_item or item, spec_for_lookup, validated_store or store)
            if looked_up > 0:
                unit = looked_up
            else:
                # 入数マスターにもない場合、品目設定のデフォルト入数を使用
                item_setting = get_item_setting(normalized_item or item)
                default_unit = item_setting.get("default_unit", 0)
                if default_unit > 0:
                    unit = default_unit

        # 数量が0の場合は警告
        if unit == 0 and boxes == 0 and remainder == 0:
            errors.append(f"行{i+1}: 数量が全て0です（店舗: {store}, 品目: {item}）")
        
        # 検証済みデータを追加
        spec_value = entry.get('spec', '')
        if spec_value is None:
            spec_value = ''
        else:
            spec_value = str(spec_value).strip()
        
        # 入数が取得できた場合、入数マスターに自動登録（新規のみ、重複はスキップ）
        if unit > 0:
            add_unit_if_new(normalized_item or item, spec_value, validated_store or store, unit)

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
    dt = datetime.strptime(shipment_date, '%Y-%m-%d')
    shipment_date_display = f"{dt.month}/{dt.day}"  # ゼロ埋めなし（例: 2/7）
    
    for entry in order_data:
        store = entry.get('store', '')
        item = entry.get('item', '')
        spec = entry.get('spec', '')
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        
        if unit == 0:
            continue
        
        # 単位を判定（get_unit_label_for_item関数を使用）
        unit_label = get_unit_label_for_item(item, spec)
        
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
    品目名と規格から単位を判定（品目設定を優先）
    
    Args:
        item: 品目名
        spec: 規格
    
    Returns:
        単位（'本'、'袋'など）
    """
    # まず品目設定から取得を試みる
    setting = get_item_setting(item)
    if setting.get("unit_type"):
        return setting["unit_type"]
    
    # 品目設定がない場合、従来のロジックで判定
    item_lower = item.lower() if item else ""
    spec_lower = spec.lower() if spec else ""
    
    # 単位を判定（品目名と規格から判定）
    unit_label = '本'  # デフォルト
    
    # 長ねぎバラの判定（品目名に「バラ」が含まれる場合）
    if '長ねぎバラ' in item or '長ネギバラ' in item or 'ネギバラ' in item or 'ねぎバラ' in item or '長ねぎばら' in item:
        unit_label = '本'
    # 長ねぎ（袋）の判定
    elif ('ネギ' in item or 'ねぎ' in item) and 'バラ' not in item and 'ばら' not in item:
        unit_label = '袋'
    # 胡瓜バラの判定（品目名に「バラ」が含まれる場合）
    elif '胡瓜バラ' in item or 'きゅうりバラ' in item or 'キュウリバラ' in item or '胡瓜ばら' in item:
        unit_label = '本'
    # 胡瓜（袋）の判定
    elif ('胡瓜' in item or 'きゅうり' in item) and 'バラ' not in item and 'ばら' not in item:
        unit_label = '袋'
    # 規格で判定（後方互換性のため）
    elif 'バラ' in spec or 'ばら' in spec_lower:
        if '胡瓜' in item or 'きゅうり' in item:
            unit_label = '本'
        elif 'ネギ' in item or 'ねぎ' in item:
            unit_label = '本'
    # その他の品目
    elif '春菊' in item or '青梗菜' in item or 'チンゲン菜' in item:
        unit_label = '袋'
    
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
    
    # Streamlit SecretsからAPIキーを読み取る（優先）
    try:
        if hasattr(st, 'secrets'):
            try:
                secrets_api_key = st.secrets.get('GEMINI_API_KEY', '')
                if secrets_api_key and not st.session_state.api_key:
                    st.session_state.api_key = secrets_api_key
                    st.info("✅ APIキーはSecretsから読み込まれました")
            except Exception:
                pass
    except Exception:
        pass
    
    # Secretsにない場合、または上書きしたい場合は手動入力
    api_key = st.text_input(
        "Gemini APIキー",
        value=st.session_state.api_key,
        type="password",
        help="Google Gemini APIのキーを入力してください（Secretsに設定されている場合は自動で読み込まれます）"
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
    1. SecretsにAPIキーを設定（または手動入力）
    2. 出荷日を選択
    3. 画像をアップロード or メールから取得
    4. 解析結果を確認・修正
    5. PDFを生成
    """)

# メインコンテンツ
if not api_key:
    st.warning("⚠️ サイドバーでGemini APIキーを入力するか、Streamlit CloudのSecretsに設定してください。")
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
    item_settings = load_item_settings()
    
    # 登録済みマスターデータ（確認・編集可能、箱数/総数切り替え）
    st.write("**📋 マスターデータ（入数・単位・受信方法）**")
    st.caption("メールの「×数字」は通常は総数です。この入数で箱数・端数を逆算します。「受信方法」を箱数にした品目は、×数字をそのまま箱数として扱います。編集して「マスターデータを保存」を押してください。")
    box_count_items = get_box_count_items()
    if item_settings:
        master_rows = []
        for name, setting in sorted(item_settings.items()):
            u = setting.get("default_unit", 0)
            t = setting.get("unit_type", "袋")
            as_boxes = setting.get("receive_as_boxes", False)
            master_rows.append({
                "品目": name,
                "1コンテナあたりの入数": u,
                "単位": t,
                "受信方法": "箱数" if as_boxes else "総数",
            })
        if master_rows:
            df_master = pd.DataFrame(master_rows)
            edited_master = st.data_editor(
                df_master,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "品目": st.column_config.TextColumn("品目", disabled=True),
                    "1コンテナあたりの入数": st.column_config.NumberColumn("1コンテナあたりの入数", min_value=1, step=1),
                    "単位": st.column_config.SelectboxColumn("単位", options=["袋", "本"], required=True),
                    "受信方法": st.column_config.SelectboxColumn("受信方法", options=["総数", "箱数"], required=True),
                },
            )
            if st.button("💾 マスターデータを保存", key="save_master_btn", type="primary"):
                for _, row in edited_master.iterrows():
                    name = str(row["品目"]).strip()
                    u = int(row["1コンテナあたりの入数"]) if row["1コンテナあたりの入数"] > 0 else 30
                    t = str(row["単位"]).strip() or "袋"
                    as_boxes = str(row["受信方法"]).strip() == "箱数"
                    set_item_setting(name, u, t, receive_as_boxes=as_boxes)
                st.success("✅ マスターデータを保存しました。解析時にこの設定が参照されます。")
                st.rerun()
    st.divider()
    
    # 新しい品目を追加（入数・単位を縦並びで確実に表示）
    st.write("**新しい品目を追加**")
    st.caption("💡 品目名、1コンテナあたりの入数、単位を入力して「追加」ボタンを押してください")
    
    new_item = st.text_input("品目名", placeholder="例: 新野菜", key="new_item_input", help="新しい品目の名前を入力してください")
    
    row1 = st.columns(2)
    with row1[0]:
        new_item_unit = st.number_input("1コンテナあたりの入数", min_value=1, value=30, step=1, key="new_item_unit_input", help="1コンテナに何個（袋/本）入るかを入力")
    with row1[1]:
        new_item_unit_type = st.selectbox("単位", ["袋", "本"], key="new_item_unit_type_input", help="単位を選択（袋 or 本）")
    
    if st.button("追加", key="add_item", type="primary"):
        if new_item and new_item.strip():
            item_name = new_item.strip()
            if add_new_item(item_name):
                set_item_setting(item_name, int(new_item_unit), new_item_unit_type)
                st.session_state[f"item_expanded_{item_name}"] = True
                st.success(f"✅ 「{item_name}」を追加しました（入数: {new_item_unit}{new_item_unit_type}/コンテナ）")
                st.rerun()
            else:
                st.warning("既に存在する品目名です")
        else:
            st.warning("品目名を入力してください")
    
    st.divider()
    
    # 登録済み品目名一覧（編集・削除可能）
    if items:
        st.write("**登録済み品目名**（各品目の **1コンテナあたりの入数** と **単位** は、下の▼をクリックして開き、中で確認・編集できます）")
        for normalized, variants in items.items():
            # 品目設定を取得
            setting = get_item_setting(normalized)
            default_unit = setting.get("default_unit", 0)
            unit_type = setting.get("unit_type", "袋")
            receive_as_boxes = setting.get("receive_as_boxes", False)
            
            # 設定情報を表示（エクスパンダー題でそのまま表示されるようプレーン文字列）
            if default_unit > 0:
                setting_info = f"入数: {default_unit}{unit_type}/コンテナ"
            else:
                setting_info = "入数: 未設定"
            if receive_as_boxes:
                setting_info += "・箱数で受信"
            
            # バリアント表示を短縮（長すぎる場合）
            variants_display = ', '.join(variants[:3])
            if len(variants) > 3:
                variants_display += f" ... (+{len(variants)-3}件)"
            
            # エクスパンダーのタイトルに品目名・入数・単位・バリアントを表示
            expander_title = f"📦 {normalized} ｜ {setting_info} ｜ バリアント: {variants_display}"
            
            # 新規追加された品目はデフォルトで展開（セッション状態で管理）
            expanded_key = f"item_expanded_{normalized}"
            if expanded_key not in st.session_state:
                st.session_state[expanded_key] = False
            
            # エクスパンダーを使用（expandedパラメータで展開状態を制御）
            with st.expander(expander_title, expanded=st.session_state.get(expanded_key, False)):
                # バリアント追加
                st.write("**表記バリアントの追加**")
                col1, col2 = st.columns([3, 1])
                with col1:
                    new_variant = st.text_input(f"「{normalized}」の新しい表記を追加", key=f"variant_{normalized}", placeholder="例: 別表記")
                with col2:
                    if st.button("追加", key=f"add_variant_{normalized}"):
                        if new_variant and new_variant.strip():
                            add_item_variant(normalized, new_variant.strip())
                            st.success(f"✅ 「{new_variant.strip()}」を追加しました")
                            st.rerun()
                
                st.divider()
                
                # 入数・単位・受信方法の設定
                st.write("**1コンテナあたりの入数・単位・受信方法の設定**")
                col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                with col1:
                    edit_unit = st.number_input(
                        "1コンテナあたりの入数",
                        min_value=1,
                        value=default_unit if default_unit > 0 else 30,
                        step=1,
                        key=f"edit_unit_{normalized}"
                    )
                with col2:
                    edit_unit_type = st.selectbox(
                        "単位",
                        ["袋", "本"],
                        index=0 if unit_type == "袋" else 1,
                        key=f"edit_unit_type_{normalized}"
                    )
                with col3:
                    edit_receive = st.selectbox(
                        "受信方法",
                        ["総数", "箱数"],
                        index=1 if receive_as_boxes else 0,
                        key=f"edit_receive_{normalized}",
                        help="メールの×数字が総数か箱数か"
                    )
                with col4:
                    if st.button("保存", key=f"save_setting_{normalized}", use_container_width=True):
                        set_item_setting(normalized, int(edit_unit), edit_unit_type, receive_as_boxes=(edit_receive == "箱数"))
                        st.success(f"✅ 「{normalized}」の設定を保存しました")
                        st.rerun()
                
                st.divider()
                
                # 品目削除
                if st.button("🗑️ この品目を削除", key=f"del_item_{normalized}", type="secondary"):
                    if remove_item(normalized):
                        remove_item_setting(normalized)
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
        
        # 入数が0の場合、品目設定のデフォルト入数を使用（表示用）
        if unit == 0:
            item_name = entry.get('item', '')
            normalized_item = normalize_item_name(item_name)
            item_setting = get_item_setting(normalized_item or item_name)
            default_unit = item_setting.get("default_unit", 0)
            if default_unit > 0:
                unit = default_unit  # 表示用にデフォルト値を設定
        
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
            normalized_item = normalize_item_name(row['品目'])
            validated_store = validate_store_name(row['店舗名']) or row['店舗名']
            try:
                spec_value = row['規格']
                if pd.isna(spec_value) or spec_value is None:
                    spec_value = ''
                else:
                    spec_value = str(spec_value).strip()
            except (KeyError, TypeError):
                spec_value = ''
            unit_val = int(row['入数(unit)'])
            if unit_val > 0:
                set_unit(normalized_item or row['品目'], spec_value, validated_store, unit_val)
            updated_data.append({
                'store': validated_store,
                'item': normalized_item,
                'spec': spec_value,
                'unit': unit_val,
                'boxes': int(row['箱数(boxes)']),
                'remainder': int(row['端数(remainder)'])
            })
        st.session_state.parsed_data = updated_data
        st.info("✅ データを更新しました。入数マスターにも反映済み。PDFを生成する場合は下のボタンを押してください。")
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
