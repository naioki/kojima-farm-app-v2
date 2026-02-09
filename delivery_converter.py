"""
v2 解析結果と納品データ（台帳）形式の相互変換

持込入力（AppSheet）とメール読み取りの両方を同じ「納品データ」台帳で扱うため、
v2 形式 [{"store","item","spec","unit","boxes","remainder"}] と
納品データ行（納品日付・農家・納品先・請求先・品目・規格・納品単価・数量・納品金額・税率 等）を変換する。
"""
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import uuid
import re


def _safe_int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    s = re.sub(r"\D", "", str(v))
    return int(s) if s else 0


def v2_result_to_delivery_rows(
    v2_result: List[Dict[str, Any]],
    delivery_date: str,
    carry_date: Optional[str] = None,
    farmer: str = "",
    store_to_dest_billing: Optional[Dict[str, Tuple[str, str]]] = None,
    default_unit_prices: Optional[Dict[str, float]] = None,
    default_tax_rate: str = "8%",
) -> List[Dict[str, Any]]:
    """
    v2 の解析結果（差し札用）を納品データの1行ずつの形式に変換する。

    Args:
        v2_result: parse_order_image の戻り値 [{"store","item","spec","unit","boxes","remainder"}]
        delivery_date: 納品日付（YYYY-MM-DD または YYYY/MM/DD）
        carry_date: 持込日付。省略時は delivery_date と同じ
        farmer: 農家名。メール読み取り由来の場合は運用で決める（共通名や未設定など）
        store_to_dest_billing: 店舗名 → (納品先, 請求先) のマップ。未指定時は store をそのまま納品先・請求先に使う
        default_unit_prices: (品目, 規格) または 品目 をキーにした単価マップ。未設定の品目は 0 になる
        default_tax_rate: 税率（"8%" または "10%"）

    Returns:
        納品データ行のリスト。各要素は 納品ID, 納品日付, 農家, 納品先, 請求先, 品目, 持込日付, 規格, 納品単価, 数量, 納品金額, 税率, チェック 等のキーを持つ
    """
    if not v2_result:
        return []

    carry = carry_date or delivery_date
    # 日付を YYYY/MM/DD に統一（スプレッドシートでよく使う形式）
    try:
        if "-" in delivery_date:
            dt = datetime.strptime(delivery_date, "%Y-%m-%d")
        else:
            dt = datetime.strptime(delivery_date, "%Y/%m/%d")
        delivery_date_str = dt.strftime("%Y/%m/%d")
    except Exception:
        delivery_date_str = delivery_date
    try:
        if "-" in carry:
            ct = datetime.strptime(carry, "%Y-%m-%d")
        else:
            ct = datetime.strptime(carry, "%Y/%m/%d")
        carry_date_str = ct.strftime("%Y/%m/%d")
    except Exception:
        carry_date_str = carry

    store_map = store_to_dest_billing or {}
    prices = default_unit_prices or {}

    rows: List[Dict[str, Any]] = []
    for rec in v2_result:
        store = (rec.get("store") or "").strip()
        item = (rec.get("item") or "").strip()
        spec = (rec.get("spec") or "").strip()
        unit = _safe_int(rec.get("unit", 0))
        boxes = _safe_int(rec.get("boxes", 0))
        remainder = _safe_int(rec.get("remainder", 0))
        quantity = (unit * boxes) + remainder
        if quantity <= 0:
            continue

        # 納品先・請求先: マスタがあればそれを使い、なければ store を両方に
        if store in store_map:
            dest, billing = store_map[store]
        else:
            dest = store
            billing = store

        # 単価: (品目, 規格) または 品目 で検索
        unit_price = 0.0
        key_spec = (item, spec)
        key_item = item
        if key_spec in prices:
            unit_price = float(prices[key_spec])
        elif key_item in prices:
            unit_price = float(prices[key_item])
        elif isinstance(prices, dict):
            # 品目名の部分一致で最初にヒットした単価を使う（フォールバック）
            for k, v in prices.items():
                if isinstance(k, str) and k and k in item:
                    unit_price = float(v)
                    break

        amount = int(round(unit_price * quantity)) if unit_price else 0

        row = {
            "納品ID": uuid.uuid4().hex[:8],
            "納品日付": delivery_date_str,
            "農家": farmer,
            "納品先": dest,
            "請求先": billing,
            "品目": item,
            "持込日付": carry_date_str,
            "規格": spec,
            "納品単価": unit_price,
            "数量": quantity,
            "納品金額": amount,
            "税率": default_tax_rate,
            "チェック": "",  # 未確定のまま追加する場合は空。確定後に ✓ 等を付与する運用可
        }
        rows.append(row)
    return rows


def delivery_rows_to_v2_format(
    delivery_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    納品データ行を v2 形式（差し札用）に戻す。過去データから差し札 PDF を再発行するときに利用する。

    納品データは「数量」を1行で持つため、v2 の unit/boxes/remainder には
    unit=1, boxes=0, remainder=数量 で変換する（1行＝1ラベルとして扱う簡易変換）。
    入数で箱・端数に分けたい場合は、呼び出し側で入数マスタを参照して分割すること。

    Args:
        delivery_rows: 納品データ行のリスト（納品先, 品目, 規格, 数量 等を含む）

    Returns:
        v2 形式のリスト [{"store","item","spec","unit","boxes","remainder"}]
    """
    v2_list: List[Dict[str, Any]] = []
    for row in delivery_rows:
        store = row.get("納品先") or row.get("store") or ""
        item = row.get("品目") or row.get("item") or ""
        spec = row.get("規格") or row.get("spec") or ""
        qty = _safe_int(row.get("数量") or row.get("quantity") or 0)
        if qty <= 0:
            continue
        v2_list.append({
            "store": store,
            "item": item,
            "spec": spec,
            "unit": 1,
            "boxes": 0,
            "remainder": qty,
        })
    return v2_list
