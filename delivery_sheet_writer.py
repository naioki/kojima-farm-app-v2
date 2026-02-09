"""
納品データ（スプレッドシート）への追記

変換済みの納品データ行を、既存の「納品データ」シートに追記する。
認証は .streamlit/secrets の GCP サービスアカウントまたは環境変数 GOOGLE_APPLICATION_CREDENTIALS の JSON パスで行う。
"""
from typing import List, Dict, Any, Optional, Tuple
import os

# 納品データシートの列順（ヘッダーと行の並びを統一）
DELIVERY_SHEET_COLUMNS = [
    "納品ID",
    "納品日付",
    "農家",
    "納品先",
    "請求先",
    "品目",
    "持込日付",
    "規格",
    "納品単価",
    "数量",
    "納品金額",
    "税率",
    "チェック",
]


def _get_credentials(st_secrets=None):
    """st.secrets または環境変数から Google 認証情報を取得し、gspread 用の credentials を返す。"""
    try:
        from google.oauth2.service_account import Credentials
    except ImportError:
        return None
    # 1) 環境変数で JSON ファイルパスが指定されている場合
    keyfile = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if keyfile and os.path.isfile(keyfile):
        try:
            return Credentials.from_service_account_file(keyfile, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        except Exception:
            pass
    # 2) Streamlit secrets に GCP サービスアカウントがある場合
    if st_secrets is not None:
        try:
            gcp = getattr(st_secrets, "gcp", None)
            if gcp is None and hasattr(st_secrets, "get"):
                gcp = st_secrets.get("gcp")
            if gcp is not None:
                info = dict(gcp) if isinstance(gcp, dict) else dict(getattr(gcp, "_raw", gcp))
                if info.get("private_key") and info.get("client_email"):
                    return Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        except Exception:
            pass
    return None


def append_delivery_rows(
    spreadsheet_id: str,
    rows: List[Dict[str, Any]],
    sheet_name: str = "納品データ",
    credentials=None,
    st_secrets=None,
) -> Tuple[bool, str]:
    """
    変換済みの納品データ行を指定スプレッドシートの「納品データ」シートに追記する。

    Args:
        spreadsheet_id: スプレッドシート ID（URL の /d/ と /edit の間の文字列）
        rows: delivery_converter.v2_result_to_delivery_rows の戻り値のような辞書のリスト
        sheet_name: シート名。既定は "納品データ"
        credentials: google.oauth2.service_account.Credentials。省略時は st_secrets または環境変数から取得
        st_secrets: Streamlit の st.secrets オブジェクト（credentials 未指定時のみ使用）

    Returns:
        (成功したか, メッセージ)
    """
    if not rows:
        return True, "追記する行がありません。"
    creds = credentials or _get_credentials(st_secrets)
    if creds is None:
        return False, "Google スプレッドシート用の認証が設定されていません。.streamlit/secrets.toml の [gcp] または GOOGLE_APPLICATION_CREDENTIALS を設定してください。"
    try:
        import gspread
    except ImportError:
        return False, "gspread がインストールされていません。pip install gspread google-auth を実行してください。"
    try:
        client = gspread.authorize(creds)
        workbook = client.open_by_key(spreadsheet_id)
        sheet = workbook.worksheet(sheet_name)
    except Exception as e:
        return False, f"スプレッドシートの取得に失敗しました: {e}"
    # 列順に従って各行をリストに
    data = []
    for row in rows:
        data.append([row.get(col, "") for col in DELIVERY_SHEET_COLUMNS])
    try:
        sheet.append_rows(data, value_input_option="USER_ENTERED")
    except Exception as e:
        return False, f"追記に失敗しました: {e}"
    return True, f"{len(data)} 行を追記しました。"


def is_sheet_configured(st_secrets=None) -> bool:
    """納品データシートへの追記が可能な認証が設定されているかどうか。"""
    return _get_credentials(st_secrets) is not None
