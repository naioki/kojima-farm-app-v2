"""
出荷ラベルPDF生成モジュール
A4用紙1枚に8分割（2列x4段）のラベルを生成（複数ページ対応）
最初のページに出荷一覧表を追加
"""
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm
from reportlab.lib.colors import black, gray
from typing import List, Dict
import os


class LabelPDFGenerator:
    """出荷ラベルPDF生成クラス"""
    
    # A4サイズ（縦）
    A4_WIDTH = 210 * mm
    A4_HEIGHT = 297 * mm
    
    # ラベルサイズ（2列x4段）
    LABEL_WIDTH = 105 * mm  # 210 / 2
    LABEL_HEIGHT = 74.25 * mm  # 297 / 4
    
    # 1ページあたりのラベル数
    LABELS_PER_PAGE = 8
    
    def __init__(self, font_path: str = None):
        """
        初期化
        
        Args:
            font_path: IPAexGothicフォントのパス（Noneの場合はデフォルトパスを試行）
        """
        self.font_path = font_path or self._find_font_path()
        self._register_font()
    
    def _find_font_path(self) -> str:
        """IPAexGothicフォントのパスを検索"""
        # 一般的なフォントパスを試行
        possible_paths = [
            'ipaexg.ttf',
            'fonts/ipaexg.ttf',
            'C:/Windows/Fonts/ipaexg.ttf',
            '/usr/share/fonts/ipaexg.ttf',
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        # フォントが見つからない場合は警告を出すが、後でエラーハンドリング
        return 'ipaexg.ttf'
    
    def _register_font(self):
        """IPAexGothicフォントを登録"""
        try:
            if os.path.exists(self.font_path):
                pdfmetrics.registerFont(TTFont('IPAGothic', self.font_path))
                self.font_available = True
            else:
                print(f"警告: フォントファイルが見つかりません: {self.font_path}")
                self.font_available = False
        except Exception as e:
            print(f"フォント登録エラー: {e}")
            self.font_available = False
    
    def _get_font_name(self) -> str:
        """使用するフォント名を返す"""
        return 'IPAGothic' if self.font_available else 'Helvetica'
    
    def generate_pdf(self, labels: List[Dict], summary_data: List[Dict], 
                    shipment_date: str, output_path: str):
        """
        PDFを生成（複数ページ対応 + 出荷一覧表）
        
        Args:
            labels: ラベル情報のリスト（全ラベル）
            summary_data: 出荷一覧表用のデータ
            shipment_date: 出荷日（YYYY-MM-DD形式）
            output_path: 出力PDFファイルパス
        """
        c = canvas.Canvas(output_path, pagesize=(self.A4_WIDTH, self.A4_HEIGHT))
        font_name = self._get_font_name()
        
        # 出荷日を表示用に変換
        from datetime import datetime
        shipment_date_obj = datetime.strptime(shipment_date, '%Y-%m-%d')
        shipment_date_display = shipment_date_obj.strftime('%m/%d')
        
        # 1ページ目：出荷一覧表
        self._draw_summary_page(c, summary_data, shipment_date_display, font_name)
        
        # 出荷一覧表の後に改ページ（ラベルページと分離）
        c.showPage()
        
        # 2ページ目以降：ラベル
        total_labels = len(labels)
        total_pages = (total_labels + self.LABELS_PER_PAGE - 1) // self.LABELS_PER_PAGE
        
        for page_idx in range(total_pages):
            if page_idx > 0:  # 2ページ目以降は改ページ
                c.showPage()
            
            start_idx = page_idx * self.LABELS_PER_PAGE
            end_idx = min(start_idx + self.LABELS_PER_PAGE, total_labels)
            page_labels = labels[start_idx:end_idx]
            
            # このページのラベルを描画
            for idx, label in enumerate(page_labels):
                label_idx = start_idx + idx
                col = label_idx % 2  # 列（0 or 1）
                row = (label_idx % self.LABELS_PER_PAGE) // 2  # 段（0-3）
                
                x = col * self.LABEL_WIDTH
                y = self.A4_HEIGHT - (row + 1) * self.LABEL_HEIGHT
                
                # ラベルを描画
                if label.get('is_fraction', False):
                    self._draw_fraction_label(c, x, y, label, font_name)
                else:
                    self._draw_standard_label(c, x, y, label, font_name)
                
                # 切断用ガイド線
                self._draw_guide_lines(c, x, y, col, row, label_idx, total_labels)
        
        c.save()
    
    def _draw_summary_page(self, c: canvas.Canvas, summary_data: List[Dict], 
                          shipment_date: str, font_name: str):
        """出荷一覧表ページを描画"""
        # フォントサイズをさらに大きく設定
        title_font_size = 32  # 24 → 32
        header_font_size = 18  # 14 → 18
        data_font_size = 16  # 14 → 16
        summary_title_font_size = 20  # 品目ごとの総数セクションのタイトル
        summary_data_font_size = 16  # 品目ごとの総数セクションのデータ
        
        # タイトル
        c.setFont(font_name, title_font_size)
        c.drawString(50 * mm, self.A4_HEIGHT - 40, f"【出荷一覧表】 {shipment_date}")
        
        # テーブルヘッダー（装飾なし）
        y_start = self.A4_HEIGHT - 70
        row_height = 20  # 行の高さをさらに増やす（18 → 20）
        header_y = y_start
        
        # ヘッダー文字（背景色なし、フォントサイズを大きく、TOTAL列を削除）
        c.setFont(font_name, header_font_size)
        c.drawString(20 * mm, header_y - 12, "店舗名")
        c.drawString(70 * mm, header_y - 12, "品目")
        c.drawString(120 * mm, header_y - 12, "フル箱")
        c.drawString(155 * mm, header_y - 12, "端数箱")
        c.drawString(180 * mm, header_y - 12, "総数")
        
        # ヘッダー下線
        c.setStrokeColor(black)
        c.setLineWidth(0.5)
        c.line(18 * mm, header_y - row_height, 190 * mm, header_y - row_height)
        
        # テーブル内容（装飾なし）
        current_y = header_y - row_height
        for entry in summary_data:
            current_y -= row_height
            
            # データ（背景色なし、フォントサイズを大きく）
            c.setFont(font_name, data_font_size)
            store = str(entry.get('store', ''))
            item = str(entry.get('item', ''))
            boxes = str(entry.get('boxes', 0))
            rem_box = str(entry.get('rem_box', 0))
            total_packs = str(entry.get('total_packs', 0))
            total_quantity = entry.get('total_quantity', 0)
            unit_label = entry.get('unit_label', '')
            
            # 総数の表示（数量 + 単位）
            total_display = f"{total_quantity}{unit_label}" if total_quantity > 0 and unit_label else str(total_quantity)
            
            c.drawString(20 * mm, current_y + 2, store)
            c.drawString(70 * mm, current_y + 2, item)
            c.drawString(120 * mm, current_y + 2, boxes)
            c.drawString(155 * mm, current_y + 2, rem_box)
            c.drawString(180 * mm, current_y + 2, total_display)
            
            # ページを超える場合は改ページ
            if current_y < 80:  # 下部に余白を確保（品目ごとの総数セクション用）
                c.showPage()
                current_y = self.A4_HEIGHT - 50
                # ヘッダーを再描画
                c.setFont(font_name, header_font_size)
                c.drawString(20 * mm, current_y - 12, "店舗名")
                c.drawString(70 * mm, current_y - 12, "品目")
                c.drawString(120 * mm, current_y - 12, "フル箱")
                c.drawString(155 * mm, current_y - 12, "端数箱")
                c.drawString(180 * mm, current_y - 12, "総数")
                # ヘッダー下線
                c.line(18 * mm, current_y - row_height, 190 * mm, current_y - row_height)
                current_y -= row_height
        
        # 品目ごとの総数セクションを追加
        # テーブルの下に余白を確保
        summary_start_y = current_y - 30
        
        # 品目ごとに集計
        from collections import defaultdict
        item_totals = defaultdict(int)
        item_units = {}
        
        for entry in summary_data:
            item = entry.get('item', '')
            spec = entry.get('spec', '').strip()
            total_quantity = entry.get('total_quantity', 0)
            unit_label = entry.get('unit_label', '')
            
            # キーをitemとspecの組み合わせにする（胡瓜の3本Pとバラを別物として扱う）
            key = (item, spec)
            item_totals[key] += total_quantity
            item_units[key] = unit_label
        
        # 品目ごとの総数セクションのタイトル
        c.setFont(font_name, summary_title_font_size)
        summary_title = f"【{shipment_date} 出荷・作成総数】"
        c.drawString(20 * mm, summary_start_y, summary_title)
        
        # 品目ごとの総数を表示
        summary_y = summary_start_y - 25
        c.setFont(font_name, summary_data_font_size)
        
        # キーをソートして表示（品目名→規格の順）
        sorted_items = sorted(item_totals.items(), key=lambda x: (x[0][0], x[0][1]))
        for (item, spec), total in sorted_items:
            unit_label = item_units.get((item, spec), '')
            # 表示形式: 品目名(規格)：数量単位
            if spec:
                display_name = f"{item}({spec})"
            else:
                display_name = item
            summary_text = f"・{display_name}：{total}{unit_label}"
            c.drawString(20 * mm, summary_y, summary_text)
            summary_y -= 20  # 次の行へ
            
            # ページを超える場合は改ページ
            if summary_y < 50:
                c.showPage()
                summary_y = self.A4_HEIGHT - 50
                # タイトルを再描画
                c.setFont(font_name, summary_title_font_size)
                c.drawString(20 * mm, summary_y, summary_title)
                summary_y -= 25
                c.setFont(font_name, summary_data_font_size)
    
    def _draw_text_in_quadrant(self, c: canvas.Canvas, text: str, font_name: str, 
                               max_font_size: int, quadrant_width: float, 
                               quadrant_height: float) -> tuple:
        """
        指定された領域内に収まるようにフォントサイズを自動調整してテキストを描画
        
        Returns:
            (font_size, text_width, text_height) のタプル
        """
        font_size = max_font_size
        text_width = c.stringWidth(text, font_name, font_size)
        text_height = font_size * 0.7  # フォント高さの概算
        
        # 領域内に収まるまでフォントサイズを縮小
        while (text_width > quadrant_width * 0.9 or 
               text_height > quadrant_height * 0.9) and font_size > 8:
            font_size -= 1
            text_width = c.stringWidth(text, font_name, font_size)
            text_height = font_size * 0.7
        
        return font_size, text_width, text_height
    
    def _draw_standard_label(self, c: canvas.Canvas, x: float, y: float, 
                            label: Dict, font_name: str):
        """通常ラベルを描画（4つの領域に厳格に分割）"""
        # ラベル枠（薄い線）
        c.setStrokeColor(gray, alpha=0.3)
        c.setLineWidth(0.5)
        c.rect(x, y, self.LABEL_WIDTH, self.LABEL_HEIGHT, stroke=1, fill=0)
        
        # テキスト色を黒に
        c.setFillColor(black)
        
        # 4つの領域のサイズ
        q_width = self.LABEL_WIDTH / 2  # 52.5mm
        q_height = self.LABEL_HEIGHT / 2  # 37.125mm
        
        # Q1: 左上 - 目的地（店舗名）を最大サイズ（中央寄せ）
        store = label.get('store', '')
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, store, font_name, 50, q_width, q_height
        )
        c.setFont(font_name, font_size)
        q1_center_x = x + q_width / 2  # Q1の中央X座標
        q1_center_y = y + self.LABEL_HEIGHT - q_height / 2  # Q1の中央Y座標
        c.drawString(q1_center_x - text_width / 2, q1_center_y - text_height / 2, store)
        
        # Q2: 右上 - コンテナ数（通し番号）（中央寄せ）
        sequence = label.get('sequence', '')
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, sequence, font_name, 40, q_width, q_height
        )
        c.setFont(font_name, font_size)
        q2_center_x = x + self.LABEL_WIDTH - q_width / 2  # Q2の中央X座標
        q2_center_y = y + self.LABEL_HEIGHT - q_height / 2  # Q2の中央Y座標
        c.drawString(q2_center_x - text_width / 2, q2_center_y - text_height / 2, sequence)
        
        # Q3: 左下 - 品目（中央寄せ）
        item = label.get('item', '')
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, item, font_name, 50, q_width, q_height
        )
        c.setFont(font_name, font_size)
        q3_center_x = x + q_width / 2  # Q3の中央X座標
        q3_center_y = y + q_height / 2  # Q3の中央Y座標
        c.drawString(q3_center_x - text_width / 2, q3_center_y - text_height / 2, item)
        
        # Q4: 右下 - 入り数（中央寄せ）
        quantity = label.get('quantity', '')
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, quantity, font_name, 30, q_width, q_height
        )
        c.setFont(font_name, font_size)
        q4_center_x = x + self.LABEL_WIDTH - q_width / 2  # Q4の中央X座標
        q4_center_y = y + q_height / 2  # Q4の中央Y座標
        c.drawString(q4_center_x - text_width / 2, q4_center_y - text_height / 2, quantity)
        
        # 出荷日（左下の隅、小さく）
        shipment_date = label.get('shipment_date', '')
        if shipment_date:
            c.setFont(font_name, 10)
            c.drawString(x + 5, y + 5, shipment_date)
    
    def _draw_fraction_label(self, c: canvas.Canvas, x: float, y: float, 
                            label: Dict, font_name: str):
        """端数ラベル（最後の1箱）を描画（4つの領域、Q4に超巨大フォント、下部に二重線）"""
        # 太い黒の破線枠
        c.setStrokeColor(black)
        c.setLineWidth(3)
        c.setDash([10, 5])  # 破線パターン
        c.rect(x + 2, y + 2, self.LABEL_WIDTH - 4, self.LABEL_HEIGHT - 4, 
              stroke=1, fill=0)
        c.setDash()  # 破線をリセット
        
        # 下部に太い二重線を描画
        c.setStrokeColor(black)
        c.setLineWidth(2)
        line_y = y + self.LABEL_HEIGHT / 2  # 中央の横線
        c.line(x + 5, line_y, x + self.LABEL_WIDTH - 5, line_y)
        c.setLineWidth(1.5)
        c.line(x + 5, line_y - 1, x + self.LABEL_WIDTH - 5, line_y - 1)
        
        # テキスト色を黒に
        c.setFillColor(black)
        
        # 4つの領域のサイズ
        q_width = self.LABEL_WIDTH / 2  # 52.5mm
        q_height = self.LABEL_HEIGHT / 2  # 37.125mm
        
        # Q1: 左上 - 目的地（店舗名）を最大サイズ（中央寄せ）
        store = label.get('store', '')
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, store, font_name, 50, q_width, q_height
        )
        c.setFont(font_name, font_size)
        q1_center_x = x + q_width / 2  # Q1の中央X座標
        q1_center_y = y + self.LABEL_HEIGHT - q_height / 2  # Q1の中央Y座標
        c.drawString(q1_center_x - text_width / 2, q1_center_y - text_height / 2, store)
        
        # Q2: 右上 - コンテナ数（通し番号）（中央寄せ）
        sequence = label.get('sequence', '')
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, sequence, font_name, 40, q_width, q_height
        )
        c.setFont(font_name, font_size)
        q2_center_x = x + self.LABEL_WIDTH - q_width / 2  # Q2の中央X座標
        q2_center_y = y + self.LABEL_HEIGHT - q_height / 2  # Q2の中央Y座標
        c.drawString(q2_center_x - text_width / 2, q2_center_y - text_height / 2, sequence)
        
        # Q3: 左下 - 品目（Q4と重ならないように幅を制限、中央寄せ）
        item = label.get('item', '')
        # Q4が超巨大フォントになるため、Q3の幅を制限（Q4のスペースを確保）
        q3_max_width = q_width * 0.8  # Q3の最大幅を80%に制限
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, item, font_name, 50, q3_max_width, q_height
        )
        c.setFont(font_name, font_size)
        q3_center_x = x + q3_max_width / 2  # Q3の中央X座標（制限された幅内）
        q3_center_y = y + q_height / 2  # Q3の中央Y座標
        c.drawString(q3_center_x - text_width / 2, q3_center_y - text_height / 2, item)
        
        # Q4: 右下 - 数量を超巨大フォント（Q3と重ならないように、中央寄せ）
        quantity = label.get('quantity', '')
        # Q4を大幅に拡張（Q3の右側のスペースも使用）
        q4_extended_width = self.LABEL_WIDTH - q3_max_width - 10  # Q3の右側まで使用
        q4_extended_height = q_height
        font_size, text_width, text_height = self._draw_text_in_quadrant(
            c, quantity, font_name, 60, q4_extended_width, q4_extended_height
        )
        c.setFont(font_name, font_size)
        q4_center_x = x + q3_max_width + 10 + q4_extended_width / 2  # Q4の中央X座標（拡張領域内）
        q4_center_y = y + q_height / 2  # Q4の中央Y座標
        c.drawString(q4_center_x - text_width / 2, q4_center_y - text_height / 2, quantity)
        
        # 出荷日（左下の隅、小さく）
        shipment_date = label.get('shipment_date', '')
        if shipment_date:
            c.setFont(font_name, 10)
            c.drawString(x + 5, y + 5, shipment_date)
    
    def _draw_guide_lines(self, c: canvas.Canvas, x: float, y: float, 
                         col: int, row: int, label_idx: int, total_labels: int):
        """切断用ガイド線を描画（極めて薄いグレー、間隔の広い破線）"""
        c.setStrokeColor(gray, alpha=0.15)  # 極めて薄いグレー
        c.setLineWidth(0.3)
        c.setDash([20, 10])  # 間隔の広い破線
        
        # 右側の縦線（最後の列以外、かつ最後のラベルでない場合）
        if col == 0 and label_idx < total_labels - 1:
            c.line(x + self.LABEL_WIDTH, y, 
                  x + self.LABEL_WIDTH, y + self.LABEL_HEIGHT)
        
        # 下側の横線（最後の段以外、かつ最後のラベルでない場合）
        if row < 3 and label_idx < total_labels - 1:
            c.line(x, y, x + self.LABEL_WIDTH, y)
        
        c.setDash()  # 破線をリセット
