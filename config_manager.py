"""
設定管理モジュール
店舗名・品目名をJSONファイルで動的に管理
"""
import json
import os
from pathlib import Path
from typing import List, Dict, Optional

CONFIG_DIR = Path("config")
STORES_FILE = CONFIG_DIR / "stores.json"
ITEMS_FILE = CONFIG_DIR / "items.json"
UNITS_FILE = CONFIG_DIR / "units.json"  # 入数マスター: 品目|規格|店舗 → 入数
ITEM_SETTINGS_FILE = CONFIG_DIR / "item_settings.json"  # 品目設定: 品目 → {default_unit, unit_type}

# デフォルト値
DEFAULT_STORES = ["鎌ケ谷", "五香", "八柱", "青葉台", "咲が丘", "習志野台", "八千代台"]

DEFAULT_ITEMS = {
    "青梗菜": ["青梗菜", "チンゲン菜", "ちんげん菜", "チンゲンサイ", "ちんげんさい"],
    "胡瓜": ["胡瓜", "きゅうり", "キュウリ", "胡瓜（袋）"],
    "胡瓜バラ": ["胡瓜バラ", "きゅうりバラ", "キュウリバラ", "胡瓜ばら"],
    "長ネギ": ["長ネギ", "ネギ", "ねぎ", "長ねぎ", "長ねぎ（袋）"],
    "長ねぎバラ": ["長ねぎバラ", "長ネギバラ", "ネギバラ", "ねぎバラ", "長ねぎばら"],
    "春菊": ["春菊", "しゅんぎく", "シュンギク"]
}

def ensure_config_dir():
    """設定ディレクトリが存在することを確認"""
    CONFIG_DIR.mkdir(exist_ok=True)

def load_stores() -> List[str]:
    """店舗名リストを読み込む"""
    ensure_config_dir()
    if STORES_FILE.exists():
        try:
            with open(STORES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('stores', DEFAULT_STORES)
        except Exception:
            return DEFAULT_STORES
    else:
        # デフォルト値を保存
        save_stores(DEFAULT_STORES)
        return DEFAULT_STORES

def save_stores(stores: List[str]):
    """店舗名リストを保存"""
    ensure_config_dir()
    with open(STORES_FILE, 'w', encoding='utf-8') as f:
        json.dump({'stores': stores}, f, ensure_ascii=False, indent=2)

def add_store(store_name: str) -> bool:
    """新しい店舗名を追加"""
    stores = load_stores()
    if store_name not in stores:
        stores.append(store_name)
        save_stores(stores)
        return True
    return False

def remove_store(store_name: str) -> bool:
    """店舗名を削除"""
    stores = load_stores()
    if store_name in stores:
        stores.remove(store_name)
        save_stores(stores)
        return True
    return False

def load_items() -> Dict[str, List[str]]:
    """品目名正規化マップを読み込む"""
    ensure_config_dir()
    if ITEMS_FILE.exists():
        try:
            with open(ITEMS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return DEFAULT_ITEMS
    else:
        # デフォルト値を保存
        save_items(DEFAULT_ITEMS)
        return DEFAULT_ITEMS

def save_items(items: Dict[str, List[str]]):
    """品目名正規化マップを保存"""
    ensure_config_dir()
    with open(ITEMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def add_item_variant(normalized_name: str, variant: str):
    """品目のバリアント（表記ゆれ）を追加"""
    items = load_items()
    if normalized_name not in items:
        items[normalized_name] = []
    if variant not in items[normalized_name]:
        items[normalized_name].append(variant)
    save_items(items)

def add_new_item(normalized_name: str, variants: Optional[List[str]] = None):
    """新しい品目を追加"""
    items = load_items()
    if normalized_name not in items:
        items[normalized_name] = variants or [normalized_name]
        save_items(items)
        return True
    return False

def remove_item(normalized_name: str) -> bool:
    """品目を削除"""
    items = load_items()
    if normalized_name in items:
        del items[normalized_name]
        save_items(items)
        return True
    return False

def auto_learn_store(store_name: str) -> str:
    """新しい店舗名を自動学習（既存のものと似ていれば統合、そうでなければ追加）"""
    stores = load_stores()
    store_name = store_name.strip()
    
    # 既存の店舗名と類似チェック
    for existing_store in stores:
        if existing_store in store_name or store_name in existing_store:
            return existing_store  # 既存の店舗名を返す
    
    # 新しい店舗名として追加
    if store_name and store_name not in stores:
        add_store(store_name)
    return store_name

def auto_learn_item(item_name: str) -> str:
    """新しい品目名を自動学習（正規化して追加）"""
    items = load_items()
    item_name = item_name.strip()
    
    # 既存の品目名と照合
    for normalized, variants in items.items():
        if item_name in variants or any(variant in item_name for variant in variants):
            return normalized
    
    # 新しい品目として追加（正規化名はそのまま使用）
    if item_name:
        add_new_item(item_name, [item_name])
    return item_name


# ==========================================
# 入数マスター（柔軟に編集可能、GASの入数マスターと同様の役割）
# - 編集した入数は次回解析時に反映され、合計数量の自動計算に使用されます
# - GASの入数マスターと同期する場合は、スプレッドシートからCSV出力して units.json に手動反映
# ==========================================

def _units_key(item: str, spec: str, store: str) -> str:
    """入数マスター用のキー生成"""
    def n(v):
        return (v or "").strip().replace(" ", "")
    return f"{n(item)}|{n(spec)}|{n(store)}"


def load_units() -> Dict[str, int]:
    """入数マスターを読み込む（品目|規格|店舗 → 入数）"""
    ensure_config_dir()
    if UNITS_FILE.exists():
        try:
            with open(UNITS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {k: int(v) for k, v in data.items() if v}
                return {}
        except Exception:
            return {}
    return {}


def save_units(units: Dict[str, int]):
    """入数マスターを保存"""
    ensure_config_dir()
    with open(UNITS_FILE, 'w', encoding='utf-8') as f:
        json.dump(units, f, ensure_ascii=False, indent=2)


def lookup_unit(item: str, spec: str, store: str) -> int:
    """入数マスターから入数を検索（0なら未登録）"""
    units = load_units()
    key = _units_key(item, spec, store)
    return units.get(key, 0)


def add_unit_if_new(item: str, spec: str, store: str, unit: int) -> bool:
    """入数マスターに登録（既存なら上書きしない、新規のみ追加）"""
    if unit <= 0:
        return False
    units = load_units()
    key = _units_key(item, spec, store)
    if key in units:
        return False  # 既存なら追加しない（柔軟に変えたい場合は上書きも可）
    units[key] = unit
    save_units(units)
    return True


def set_unit(item: str, spec: str, store: str, unit: int) -> None:
    """入数マスターの入数を設定（既存は上書き＝柔軟に変えられる）"""
    if unit <= 0:
        return
    units = load_units()
    key = _units_key(item, spec, store)
    units[key] = unit
    save_units(units)


def initialize_default_units():
    """デフォルト入数を初期化（全店舗共通のデフォルト値）"""
    units = load_units()
    updated = False
    
    # デフォルト入数の定義（品目|規格 → 入数）
    default_unit_map = {
        ("胡瓜", ""): 30,  # 胡瓜（袋）: 30袋/コンテナ
        ("胡瓜バラ", ""): 100,  # 胡瓜バラ: 100本/コンテナ
        ("長ネギ", ""): 50,  # 長ねぎ: 50本/コンテナ
        ("長ねぎバラ", ""): 50,  # 長ねぎバラ: 50本/コンテナ
        ("春菊", ""): 30,  # 春菊: 30袋/コンテナ
        ("青梗菜", ""): 20,  # 青梗菜: 20袋/コンテナ
    }
    
    # 全店舗にデフォルト値を設定（既存の値がある場合は上書きしない）
    stores = load_stores()
    for (item, spec), unit in default_unit_map.items():
        for store in stores:
            key = _units_key(item, spec, store)
            if key not in units:  # 既存の値がない場合のみ設定
                units[key] = unit
                updated = True
    
    if updated:
        save_units(units)


# ==========================================
# 品目設定管理（1コンテナあたりの入数と単位）
# ==========================================

DEFAULT_ITEM_SETTINGS = {
    "胡瓜": {"default_unit": 30, "unit_type": "袋"},
    "胡瓜バラ": {"default_unit": 100, "unit_type": "本"},
    "長ネギ": {"default_unit": 50, "unit_type": "本"},
    "長ねぎバラ": {"default_unit": 50, "unit_type": "本"},
    "春菊": {"default_unit": 30, "unit_type": "袋"},
    "青梗菜": {"default_unit": 20, "unit_type": "袋"},
}

def load_item_settings() -> Dict[str, Dict[str, any]]:
    """品目設定を読み込む（品目 → {default_unit, unit_type}）"""
    ensure_config_dir()
    if ITEM_SETTINGS_FILE.exists():
        try:
            with open(ITEM_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # 既存の設定にデフォルト値をマージ（存在しない品目を追加）
                    merged = DEFAULT_ITEM_SETTINGS.copy()
                    merged.update(data)
                    # 長ねぎ・長ねぎバラの設定を確実に50本に設定（複数の表記に対応）
                    for key in ["長ネギ", "長ねぎバラ", "長ネギバラ"]:
                        if key in merged:
                            merged[key] = {"default_unit": 50, "unit_type": "本"}
                    # マージした結果を保存（デフォルト値が確実に含まれる）
                    save_item_settings(merged)
                    return merged
                return DEFAULT_ITEM_SETTINGS.copy()
        except Exception:
            # エラー時はデフォルト値を保存して返す
            save_item_settings(DEFAULT_ITEM_SETTINGS)
            return DEFAULT_ITEM_SETTINGS.copy()
    else:
        # デフォルト値を保存
        save_item_settings(DEFAULT_ITEM_SETTINGS)
        return DEFAULT_ITEM_SETTINGS.copy()


def save_item_settings(settings: Dict[str, Dict[str, any]]):
    """品目設定を保存"""
    ensure_config_dir()
    with open(ITEM_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def get_item_setting(item: str) -> Dict[str, any]:
    """品目の設定を取得（デフォルト値あり）"""
    settings = load_item_settings()
    if item in settings:
        return settings[item]
    # デフォルト値を返す
    return {"default_unit": 0, "unit_type": "袋"}


def set_item_setting(item: str, default_unit: int, unit_type: str):
    """品目の設定を設定・更新"""
    settings = load_item_settings()
    settings[item] = {
        "default_unit": default_unit,
        "unit_type": unit_type
    }
    save_item_settings(settings)


def remove_item_setting(item: str):
    """品目の設定を削除"""
    settings = load_item_settings()
    if item in settings:
        del settings[item]
        save_item_settings(settings)
