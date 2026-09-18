"""
PostgreSQL Database Statistics Exporter  v3.0  (Y tế / HIS-EMR)
════════════════════════════════════════════════════════════════
Sheets:
  1. Thông tin bảng   – phân loại, index/trigger count, ts cols, tốc độ tăng trưởng
  2. Schema           – mục đích suy luận
  3. Index            – danh sách index
  4. Trigger          – danh sách trigger
  5. FK ngầm          – cột *_id/*_code không có FK constraint
  6. Chi tiết cột     – data type, key, PII/PHI auto-detect
  7. Thống kê cột     – cardinality, null%, min/max/avg, mẫu (che PII)
  8. Quan hệ bảng     – foreign keys chính thức
  Info                – metadata báo cáo
"""

import re
import sys
from datetime import datetime, time

import psycopg2
import psycopg2.extras
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ─────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH KẾT NỐI – chỉnh tại đây
# ─────────────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     "",
    "port":     5432,
    "dbname":   "",
    "user":     "",
    "password": "",
}
OUTPUT_FILE = ""   # để trống → tự đặt tên: pg_stats_<dbname>_<timestamp>.xlsx

# Thêm các cặp schema.table.column bổ sung nếu auto-detect chưa đủ:
EXTRA_PII_COLUMNS: set = {
    # "public.patients.mat_khau",
}

# ─────────────────────────────────────────────────────────────────────────────
# COLOUR PALETTE
# ─────────────────────────────────────────────────────────────────────────────
C_TITLE  = "1F4E79"
C_HDR    = "2E75B6"
C_WHITE  = "FFFFFF"
C_ALT    = "DEEAF1"
C_PK     = "E2EFDA"   # xanh lá nhạt  – primary key
C_FK     = "FFF2CC"   # vàng nhạt      – foreign key
C_PII    = "FCE4D6"   # hồng cam       – PII/PHI
C_WARN   = "FFE699"   # hổ phách       – cảnh báo / FK ngầm / null cao
C_BORDER = "BDD7EE"

# ─────────────────────────────────────────────────────────────────────────────
# PII / PHI AUTO-DETECT  (lĩnh vực Y tế – tiếng Việt + tiếng Anh)
# ─────────────────────────────────────────────────────────────────────────────
PII_PATTERNS = [
    # Họ tên
    r"ho_ten", r"hoten", r"full_?name", r"first_?name", r"last_?name",
    r"ten_benh_nhan", r"ten_bn", r"patient_?name",
    # CMND / CCCD / Passport
    r"cmnd", r"cccd", r"so_cmnd", r"id_?card", r"passport",
    r"so_ho_chieu", r"identity",
    # Số thẻ BHYT / BHXH
    r"bh_?yt", r"bhyt", r"bhxh", r"ma_the", r"so_the",
    r"insurance_?number", r"insurance_?id", r"health_?card",
    # Địa chỉ
    r"dia_chi", r"diachi", r"address", r"street",
    r"xa_phuong", r"quan_huyen", r"tinh_thanh",
    # Số điện thoại
    r"so_dt", r"sdt", r"dien_thoai", r"phone", r"mobile", r"tel",
    r"so_dien_thoai",
    # Email
    r"email", r"e_mail",
    # Ngày sinh
    r"ngay_sinh", r"ngaysinh", r"dob", r"birth_?date", r"date_of_birth",
    # Mã bệnh nhân (định danh cá nhân)
    r"ma_bn", r"mabn", r"patient_?id", r"benh_nhan_id",
    # Dân tộc / tôn giáo / nghề nghiệp (PHI)
    r"dan_toc", r"ton_giao", r"religion", r"ethnicity",
    r"nghe_nghiep", r"occupation",
    # Số tài khoản ngân hàng
    r"so_tk", r"bank_?account", r"account_?number",
    # Chẩn đoán / bệnh lý (PHI y tế)
    r"chan_doan", r"diagnosis", r"icd_?code", r"benh_chinh",
]
_PII_RE = re.compile("|".join(PII_PATTERNS), re.IGNORECASE)


def auto_pii(schema: str, table: str, col: str) -> bool:
    """Trả về True nếu tên cột khớp bất kỳ pattern PII/PHI."""
    if f"{schema}.{table}.{col}" in EXTRA_PII_COLUMNS:
        return True
    return bool(_PII_RE.search(col))


def mask_pii_sample(sample: str) -> str:
    """Che giá trị mẫu PII: giữ 2 ký tự đầu, thay phần còn lại bằng ***"""
    parts = []
    for v in sample.split(" | "):
        v = v.strip()
        parts.append((v[:2] + "***") if len(v) > 2 else "***")
    return " | ".join(parts)


# Regex xóa ký tự không hợp lệ trong Excel (control chars trừ tab/newline thông thường)
_ILLEGAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]")

def clean_cell(value):
    """Làm sạch giá trị trước khi ghi vào Excel: xóa ký tự điều khiển, giới hạn độ dài."""
    if not isinstance(value, str):
        return value
    value = _ILLEGAL_CHARS_RE.sub("", value)   # xóa control chars
    value = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return value[:500]   # giới hạn 500 ký tự phòng cell quá dài


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICATION KEYWORDS
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA_PURPOSE = {
    "audit":    ("audit","log","history","changelog","trail"),
    "archive":  ("archive","archiv","bak","backup","old","cu"),
    "staging":  ("staging","stg","tmp","temp","import","load","nhap"),
    "log":      ("log","event","trace","monitor","nhat_ky"),
}

TBL_RULES = [
    # ── Không sử dụng / tạm ────────────────────────────────────────────────
    ("Không sử dụng",           ("_old","_bak","_bk","_backup","_unused","_del",
                                  "_deleted","khong_dung","khongdung")),
    ("Không sử dụng",           ("tmp_","temp_","test_","_test","_tmp","_temp")),
    ("Mới áp dụng",             ("_new","_v2","_v3","_draft","moi_","new_")),

    # ── Hệ thống / audit ───────────────────────────────────────────────────
    ("Hệ thống",                ("sys_","config","setting","parameter","he_thong",
                                  "hethuong","permission","role","user_","users",
                                  "account","menu","module","function_","chuc_nang")),
    ("Hệ thống",                ("audit","log","history","trail","change_log",
                                  "event_log","nhat_ky","lich_su")),

    # ── Danh mục phân loại chi tiết ────────────────────────────────────────
    ("Danh mục / ICD",          ("icd","icdo","icd10","icd9","ma_benh",
                                  "danh_muc_benh","dm_benh")),
    ("Danh mục / Dược",         ("dm_thuoc","danh_muc_thuoc","thuoc_","_thuoc",
                                  "duoc_","hoat_chat","nong_do","ham_luong",
                                  "dang_bao_che","dm_duoc","nhom_thuoc")),
    ("Danh mục / Vật tư",       ("vat_tu","vattu","dm_vt","danh_muc_vt",
                                  "cong_cu","dung_cu","thiet_bi","trang_thiet")),
    ("Danh mục / Cận lâm sàng", ("dm_xet_nghiem","dm_cdha","dm_cls",
                                  "danh_muc_xn","danh_muc_cdha","loai_xn",
                                  "nhom_xn","may_xet_nghiem")),
    ("Danh mục / Khám bệnh",    ("dm_dich_vu","danh_muc_dv","dm_kham",
                                  "dich_vu_kham","goi_dich_vu","loai_dv")),
    ("Danh mục / Tài chính",    ("dm_nguon","dm_thu","dm_chi","dm_bh",
                                  "danh_muc_thu","muc_gia","bang_gia",
                                  "gia_dich_vu","phu_cap")),
    ("Danh mục / Bệnh nhân",    ("dm_dan_toc","dm_nghe_nghiep","dm_quoc_tich",
                                  "dm_doi_tuong","loai_benh_nhan","doi_tuong")),
    ("Danh mục / Bệnh án",      ("dm_loai_ba","dm_khoa","danh_muc_khoa",
                                  "dm_buong","dm_giuong","khoa_phong",
                                  "buong_benh","giuong_benh")),
    ("Danh mục / Báo cáo",      ("dm_bao_cao","mau_bao_cao","template_bc")),
    ("Danh mục / Hệ thống",     ("dm_","danh_muc","danhmuc","category",
                                  "catalog","lookup","dm")),

    # ── Bác sĩ / nhân viên ────────────────────────────────────────────────
    ("Bác sĩ",                  ("bac_si","bacsi","nhan_vien","nhanvien",
                                  "nv_","staff","doctor","physician",
                                  "y_ta","yta","nurse","ho_sinh","dieu_duong")),

    # ── Bệnh nhân ─────────────────────────────────────────────────────────
    ("Bệnh nhân",               ("benh_nhan","benhnhan","patient","bn_",
                                  "ho_so_bn","thong_tin_bn","tiep_nhan")),

    # ── Bệnh án ───────────────────────────────────────────────────────────
    ("Bệnh án",                 ("benh_an","benhán","benh_an","medical_record",
                                  "ho_so_ba","noi_tru","ngoai_tru_ba",
                                  "phau_thuat","phieu_phau","mo_","_mo_",
                                  "gay_me","gay_te","ho_so_benh_an")),

    # ── Khám bệnh nội trú ──────────────────────────────────────────────────
    ("Khám bệnh / nội trú",     ("noi_tru","noitru","inpatient","nhap_vien",
                                  "xuat_vien","chuyen_vien","buong_","giuong_",
                                  "dieu_tri_","y_lenh","yêu_cau_kham")),

    # ── Khám bệnh (ngoại trú / chung) ─────────────────────────────────────
    ("Khám bệnh",               ("kham_benh","khambenh","luot_kham","luotkham",
                                  "phong_kham","visit","encounter","appointment",
                                  "lich_kham","dang_ky_kham","tiep_don",
                                  "chi_dinh","kq_kham")),

    # ── Cận lâm sàng ──────────────────────────────────────────────────────
    ("Cận lâm sàng",            ("xet_nghiem","xetnghiem","cdha","sieu_am",
                                  "xray","x_ray","ct_scan","mri","noi_soi",
                                  "lab_","laboratory","specimen","mau_xn",
                                  "kq_xn","kq_cdha","cls_","phieu_xn")),

    # ── Dược ──────────────────────────────────────────────────────────────
    ("Dược",                    ("don_thuoc","donthuoc","toa_thuoc","toathuoc",
                                  "prescription","cap_phat","xuat_thuoc",
                                  "nhap_thuoc","ton_kho_thuoc","kho_thuoc",
                                  "phieu_linh","duoc_ngoai_tru","duoc_noi_tru")),

    # ── Vật tư ────────────────────────────────────────────────────────────
    ("Vật tư",                  ("nhap_vt","xuat_vt","ton_kho_vt","kho_vt",
                                  "phieu_vt","cap_phat_vt","kiem_ke")),

    # ── Tài chính ──────────────────────────────────────────────────────────
    ("Tài chính",               ("hoa_don","hoadon","invoice","thanh_toan",
                                  "payment","thu_phi","tam_ung","hoan_tra",
                                  "quyet_toan","bao_hiem","bhyt_","chi_phi",
                                  "cong_no","thu_chi","tai_chinh")),

    # ── Báo cáo ───────────────────────────────────────────────────────────
    ("Báo cáo",                 ("bao_cao","baocao","report","thong_ke",
                                  "thongke","tong_hop","summary","rpt_","bc_")),
]

# Màu tương ứng từng loại (dùng trong Sheet 1)
CL_COLORS = {
    "Bác sĩ":                   "1F4E79",
    "Báo cáo":                  "7030A0",
    "Bệnh án":                  "833C00",
    "Bệnh nhân":                "375623",
    "Cận lâm sàng":             "0070C0",
    "Danh mục / Báo cáo":      "7030A0",
    "Danh mục / Bệnh án":      "833C00",
    "Danh mục / Bệnh nhân":    "375623",
    "Danh mục / Cận lâm sàng": "0070C0",
    "Danh mục / Dược":          "00B050",
    "Danh mục / Hệ thống":     "595959",
    "Danh mục / ICD":           "C55A11",
    "Danh mục / Khám bệnh":    "2E75B6",
    "Danh mục / Tài chính":    "C00000",
    "Danh mục / Vật tư":       "7B7B00",
    "Dược":                     "00B050",
    "Hệ thống":                 "595959",
    "Khám bệnh":                "2E75B6",
    "Khám bệnh / nội trú":     "1F4E79",
    "Không sử dụng":            "C00000",
    "Mới áp dụng":              "FF6600",
    "Tài chính":                "C00000",
    "Vật tư":                   "7B7B00",
}


def classify_table(name: str) -> str:
    low = name.lower()
    for label, kws in TBL_RULES:
        if any(k in low for k in kws):
            return label
    return "Hệ thống"

NUMERIC_TYPES  = {
    "smallint","integer","bigint","decimal","numeric","real",
    "double precision","serial","bigserial","money",
}
DATETIME_TYPES = {
    "date","timestamp without time zone","timestamp with time zone",
    "timestamptz","time without time zone","time with time zone",
}


# ─────────────────────────────────────────────────────────────────────────────
# STYLE HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _border():
    s = Side(style="thin", color=C_BORDER)
    return Border(left=s, right=s, top=s, bottom=s)


def style_title(ws, title, ncols):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=title)
    c.font      = Font(name="Arial", bold=True, size=13, color=C_WHITE)
    c.fill      = PatternFill("solid", fgColor=C_TITLE)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28


def style_hdr(cell):
    cell.font      = Font(name="Arial", bold=True, color=C_WHITE, size=10)
    cell.fill      = PatternFill("solid", fgColor=C_HDR)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border    = _border()
    return cell


def style_cell(cell, bg=None, bold=False, center=False, italic=False, color=None):
    cell.font      = Font(name="Arial", size=10, bold=bold, italic=italic,
                          color=color or "000000")
    cell.alignment = Alignment(vertical="center",
                                horizontal="center" if center else "left")
    cell.border    = _border()
    if bg:
        cell.fill = PatternFill("solid", fgColor=bg)
    return cell


def write_headers(ws, hdrs, row=2):
    for ci, h in enumerate(hdrs, 1):
        style_hdr(ws.cell(row=row, column=ci, value=h))
    ws.row_dimensions[row].height = 22
    ws.freeze_panes = f"A{row + 1}"


def auto_width(ws, mn=10, mx=60):
    for col in ws.columns:
        w = max((len(str(c.value or "")) for c in col), default=mn)
        ws.column_dimensions[get_column_letter(col[0].column)].width = \
            min(max(w + 2, mn), mx)


def alt(ri):
    return C_ALT if ri % 2 == 0 else None


def classify_schema(name):
    low = name.lower()
    for purpose, kws in SCHEMA_PURPOSE.items():
        if any(k in low for k in kws):
            return purpose
    return "production"


# ─────────────────────────────────────────────────────────────────────────────
# TIME INPUT HELPER (THÊM CƠ CHẾ INPUT USER)
# ─────────────────────────────────────────────────────────────────────────────
def get_time_input(prompt_text, is_end=False):
    while True:
        val = input(prompt_text).strip()
        if not val:
            return None

        # Nếu người dùng chỉ nhập ngày YYYY-MM-DD:
        # - start_time lấy đầu ngày 00:00:00
        # - end_time lấy cuối ngày 23:59:59
        try:
            dt = datetime.strptime(val, "%Y-%m-%d")
            if is_end:
                return datetime.combine(dt.date(), time(23, 59, 59))
            return dt
        except ValueError:
            pass

        try:
            return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print("   ⚠️ Sai định dạng. Vui lòng nhập chuẩn YYYY-MM-DD hoặc YYYY-MM-DD HH:MM:SS (hoặc Enter để bỏ qua).")


def calc_calendar_months_inclusive(start_time, end_time):
    """
    Tính số tháng theo logic báo cáo tháng, tính cả tháng đầu và tháng cuối.
    Ví dụ: 2025-01-01 → 2026-05-31 = 17 tháng.
    """
    return (end_time.year - start_time.year) * 12 + (end_time.month - start_time.month) + 1



def get_table_input(prompt_text):
    """
    Nhận danh sách bảng người dùng muốn tổng hợp.

    Cách nhập:
      - Bỏ trống: quét toàn bộ bảng trong database.
      - Nhập tên bảng, cách nhau bằng dấu phẩy: "A, B".
      - Có thể nhập kèm schema nếu cần: "public.A, his.B".

    Hàm trả về list token đã chuẩn hóa lower-case, hoặc None nếu người dùng bỏ trống.
    """
    raw = input(prompt_text).strip()
    if not raw:
        return None

    tokens = []
    for item in raw.split(","):
        token = item.strip().strip('"').strip("'")
        if token:
            tokens.append(token.lower())

    return tokens or None


def _table_matches_token(schema_name, table_name, token):
    """Match input token theo table hoặc schema.table, không phân biệt hoa/thường."""
    schema_name = schema_name.lower()
    table_name = table_name.lower()
    full_name = f"{schema_name}.{table_name}"
    return token == table_name or token == full_name


def filter_report_data_by_tables(schemas, tables, impl_fk, columns, relations, selected_tokens):
    """
    Lọc toàn bộ dữ liệu report theo danh sách bảng đã nhập.

    Nếu selected_tokens = None: giữ nguyên toàn bộ database.
    Nếu có input: chỉ giữ các sheet liên quan đến bảng đã match.
    """
    if not selected_tokens:
        return schemas, tables, impl_fk, columns, relations

    selected_keys = {
        (r["schema_name"], r["table_name"])
        for r in tables
        if any(_table_matches_token(r["schema_name"], r["table_name"], token)
               for token in selected_tokens)
    }

    matched_tokens = {
        token
        for token in selected_tokens
        if any(_table_matches_token(r["schema_name"], r["table_name"], token)
               for r in tables)
    }
    unmatched_tokens = [token for token in selected_tokens if token not in matched_tokens]

    if unmatched_tokens:
        print("   ⚠️ Không tìm thấy bảng:", ", ".join(unmatched_tokens))

    if not selected_keys:
        print("❌ Không có bảng nào khớp với input. Dừng chương trình để tránh xuất nhầm toàn bộ database.")
        sys.exit(1)

    filtered_tables = [
        r for r in tables
        if (r["schema_name"], r["table_name"]) in selected_keys
    ]
    filtered_columns = [
        r for r in columns
        if (r["table_schema"], r["table_name"]) in selected_keys
    ]
    filtered_impl_fk = [
        r for r in impl_fk
        if (r["table_schema"], r["table_name"]) in selected_keys
    ]

    # Quan hệ FK được tính theo bảng nguồn (from_table), vì constraint thuộc về bảng đó.
    filtered_relations = [
        r for r in relations
        if (r["from_schema"], r["from_table"]) in selected_keys
    ]

    selected_schema_names = {schema for schema, _ in selected_keys}
    selected_table_count_by_schema = {
        schema: sum(1 for s, _ in selected_keys if s == schema)
        for schema in selected_schema_names
    }
    filtered_schemas = []
    for r in schemas:
        if r["schema_name"] in selected_schema_names:
            rr = dict(r)
            rr["table_count"] = selected_table_count_by_schema.get(r["schema_name"], 0)
            rr["total_size"] = "Theo bảng đã chọn"
            filtered_schemas.append(rr)

    print(f"   🎯 Đã lọc theo input: {len(filtered_tables)} bảng được chọn")
    return filtered_schemas, filtered_tables, filtered_impl_fk, filtered_columns, filtered_relations

# ─────────────────────────────────────────────────────────────────────────────
# SQL QUERIES
# ─────────────────────────────────────────────────────────────────────────────
SQL_SCHEMAS = """
SELECT
    n.nspname                                           AS schema_name,
    obj_description(n.oid, 'pg_namespace')              AS schema_comment,
    COUNT(DISTINCT c.relname)                           AS table_count,
    pg_size_pretty(
        COALESCE(SUM(pg_total_relation_size(c.oid)),0)) AS total_size
FROM pg_namespace n
LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind = 'r'
WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
  AND n.nspname NOT LIKE 'pg_temp_%'
GROUP BY n.nspname, n.oid
ORDER BY n.nspname;
"""

SQL_TABLES = """
SELECT
    t.table_schema                                          AS schema_name,
    t.table_name,
    obj_description(pc.oid,'pg_class')                     AS table_comment,
    COALESCE(s.n_live_tup, 0)                              AS row_count,
    COUNT(DISTINCT c.column_name)                           AS column_count,
    pg_size_pretty(pg_total_relation_size(pc.oid))         AS total_size,
    pg_size_pretty(pg_relation_size(pc.oid))               AS data_size,
    pg_size_pretty(pg_indexes_size(pc.oid))                AS index_size,
    (SELECT COUNT(*) FROM information_schema.table_constraints x
     WHERE x.table_schema=t.table_schema AND x.table_name=t.table_name
       AND x.constraint_type='PRIMARY KEY')                AS has_pk,
    (SELECT COUNT(*) FROM information_schema.table_constraints x
     WHERE x.table_schema=t.table_schema AND x.table_name=t.table_name
       AND x.constraint_type='FOREIGN KEY')                AS fk_count,
    (SELECT STRING_AGG(column_name,', ')
     FROM information_schema.columns c2
     WHERE c2.table_schema=t.table_schema AND c2.table_name=t.table_name
       AND c2.data_type IN ('timestamp without time zone',
                             'timestamp with time zone','date')
       AND c2.column_name ~* '(creat|insert|updat|modif|chang|timestamp|ngay|thoigian|date|time|version)')
                                                           AS ts_columns,
    t.table_type
FROM information_schema.tables t
JOIN information_schema.columns c
    ON c.table_schema=t.table_schema AND c.table_name=t.table_name
LEFT JOIN pg_class pc
    ON pc.relname=t.table_name
   AND pc.relnamespace=(SELECT oid FROM pg_namespace WHERE nspname=t.table_schema)
LEFT JOIN pg_stat_user_tables s
    ON s.schemaname=t.table_schema AND s.relname=t.table_name
WHERE t.table_schema NOT IN ('pg_catalog','information_schema')
  AND t.table_type='BASE TABLE'
GROUP BY t.table_schema, t.table_name, pc.oid,
         obj_description(pc.oid,'pg_class'), s.n_live_tup,
         s.last_analyze, t.table_type
ORDER BY t.table_schema, t.table_name;
"""

SQL_INDEXES = """
SELECT
    schemaname                                        AS schema_name,
    tablename                                         AS table_name,
    indexname                                         AS index_name,
    CASE WHEN indexname IN (
             SELECT conname FROM pg_constraint WHERE contype='p')
         THEN 'YES' ELSE 'NO' END                    AS is_pk,
    CASE WHEN indexname IN (
             SELECT conname FROM pg_constraint WHERE contype='u')
         THEN 'YES' ELSE 'NO' END                    AS is_unique,
    indexdef                                          AS index_def
FROM pg_indexes
WHERE schemaname NOT IN ('pg_catalog','information_schema')
ORDER BY schemaname, tablename, indexname;
"""

SQL_TRIGGERS = """
SELECT
    n.nspname                                         AS schema_name,
    c.relname                                         AS table_name,
    t.tgname                                          AS trigger_name,
    CASE t.tgtype & 2 WHEN 2 THEN 'BEFORE' ELSE 'AFTER' END AS timing,
    CASE
        WHEN t.tgtype & 4  > 0 THEN 'INSERT'
        WHEN t.tgtype & 8  > 0 THEN 'DELETE'
        WHEN t.tgtype & 16 > 0 THEN 'UPDATE'
        ELSE 'OTHER'
    END                                               AS event,
    p.proname                                         AS function_name,
    t.tgenabled::text                                 AS enabled
FROM pg_trigger t
JOIN pg_class     c ON c.oid=t.tgrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_proc      p ON p.oid=t.tgfoid
WHERE NOT t.tgisinternal
  AND n.nspname NOT IN ('pg_catalog','information_schema')
ORDER BY n.nspname, c.relname, t.tgname;
"""

SQL_IMPLICIT_FK = """
SELECT
    c.table_schema,
    c.table_name,
    c.column_name,
    c.data_type,
    REGEXP_REPLACE(c.column_name,'_(id|code|fk)$','','i') AS likely_ref_table
FROM information_schema.columns c
WHERE c.table_schema NOT IN ('pg_catalog','information_schema')
  AND c.column_name ~* '_(id|code|fk)$'
  AND NOT EXISTS (
      SELECT 1 FROM information_schema.key_column_usage kcu
      JOIN information_schema.table_constraints tc
          ON tc.constraint_name=kcu.constraint_name
         AND tc.table_schema=kcu.table_schema
      WHERE tc.constraint_type='FOREIGN KEY'
        AND kcu.table_schema=c.table_schema
        AND kcu.table_name=c.table_name
        AND kcu.column_name=c.column_name)
  AND NOT EXISTS (
      SELECT 1 FROM information_schema.key_column_usage kcu
      JOIN information_schema.table_constraints tc
          ON tc.constraint_name=kcu.constraint_name
         AND tc.table_schema=kcu.table_schema
      WHERE tc.constraint_type='PRIMARY KEY'
        AND kcu.table_schema=c.table_schema
        AND kcu.table_name=c.table_name
        AND kcu.column_name=c.column_name)
ORDER BY c.table_schema, c.table_name, c.column_name;
"""

SQL_COLUMNS = """
SELECT
    c.table_schema,
    c.table_name,
    c.ordinal_position                              AS col_order,
    c.column_name,
    c.data_type,
    CASE
        WHEN c.character_maximum_length IS NOT NULL
             THEN c.data_type||'('||c.character_maximum_length||')'
        WHEN c.numeric_precision IS NOT NULL
             AND c.data_type IN ('numeric','decimal')
             THEN c.data_type||'('||c.numeric_precision||','
                  ||COALESCE(c.numeric_scale,0)||')'
        ELSE c.data_type
    END                                             AS full_type,
    c.is_nullable,
    c.column_default,
    CASE WHEN pk.column_name IS NOT NULL THEN 'PK' ELSE '' END AS pk,
    CASE WHEN fk.column_name IS NOT NULL THEN 'FK' ELSE '' END AS fk,
    CASE WHEN uk.column_name IS NOT NULL THEN 'UQ' ELSE '' END AS uq,
    col_description(pc.oid, c.ordinal_position)     AS col_comment
FROM information_schema.columns c
LEFT JOIN pg_class pc
    ON pc.relname=c.table_name
   AND pc.relnamespace=(SELECT oid FROM pg_namespace WHERE nspname=c.table_schema)
LEFT JOIN (
    SELECT kcu.table_schema,kcu.table_name,kcu.column_name
    FROM information_schema.key_column_usage kcu
    JOIN information_schema.table_constraints tc
        ON tc.constraint_name=kcu.constraint_name
       AND tc.table_schema=kcu.table_schema
    WHERE tc.constraint_type='PRIMARY KEY'
) pk ON pk.table_schema=c.table_schema AND pk.table_name=c.table_name
     AND pk.column_name=c.column_name
LEFT JOIN (
    SELECT kcu.table_schema,kcu.table_name,kcu.column_name
    FROM information_schema.key_column_usage kcu
    JOIN information_schema.table_constraints tc
        ON tc.constraint_name=kcu.constraint_name
       AND tc.table_schema=kcu.table_schema
    WHERE tc.constraint_type='FOREIGN KEY'
) fk ON fk.table_schema=c.table_schema AND fk.table_name=c.table_name
     AND fk.column_name=c.column_name
LEFT JOIN (
    SELECT kcu.table_schema,kcu.table_name,kcu.column_name
    FROM information_schema.key_column_usage kcu
    JOIN information_schema.table_constraints tc
        ON tc.constraint_name=kcu.constraint_name
       AND tc.table_schema=kcu.table_schema
    WHERE tc.constraint_type='UNIQUE'
) uk ON uk.table_schema=c.table_schema AND uk.table_name=c.table_name
     AND uk.column_name=c.column_name
WHERE c.table_schema NOT IN ('pg_catalog','information_schema')
ORDER BY c.table_schema, c.table_name, c.ordinal_position;
"""

SQL_RELATIONS = """
SELECT
    tc.constraint_name,
    tc.table_schema                  AS from_schema,
    tc.table_name                    AS from_table,
    kcu.column_name                  AS from_column,
    ccu.table_schema                 AS to_schema,
    ccu.table_name                   AS to_table,
    ccu.column_name                  AS to_column,
    rc.update_rule,
    rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON kcu.constraint_name=tc.constraint_name
   AND kcu.table_schema=tc.table_schema
JOIN information_schema.constraint_column_usage ccu
    ON ccu.constraint_name=tc.constraint_name
   AND ccu.table_schema=tc.table_schema
JOIN information_schema.referential_constraints rc
    ON rc.constraint_name=tc.constraint_name
   AND rc.constraint_schema=tc.table_schema
WHERE tc.constraint_type='FOREIGN KEY'
  AND tc.table_schema NOT IN ('pg_catalog','information_schema')
ORDER BY tc.table_schema, tc.table_name, kcu.column_name;
"""


# ─────────────────────────────────────────────────────────────────────────────
# COLUMN STATISTICS & GROWTH RATE (Đã thay thế bằng thuật toán mới)
# ─────────────────────────────────────────────────────────────────────────────
def compute_growth(conn, tables, start_time=None, end_time=None):
    """
    1. Quét toàn bộ cột timestamp trong bảng, dùng COUNT(col) để tìm cột có dữ liệu đầy đủ nhất.
    2. Nếu người dùng nhập Timeframe (start_time, end_time): Đếm dòng lọt vào mốc thời gian đó và chia số tháng.
    3. Nếu không nhập Timeframe: Lấy Max(Count) chia cho (MAX(thời gian) - MIN(thời gian)).
    """
    result = {}
    cur    = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    todo   = [(r["schema_name"], r["table_name"], r["ts_columns"], int(r["row_count"] or 0))
              for r in tables if r.get("ts_columns")]

    print(f"   Tính tăng trưởng cho {len(todo)} bảng có timestamp...", end=" ", flush=True)
    for schema, table, ts_cols_str, row_count in todo:
        if row_count == 0:
            result[(schema, table)] = {"growth": None, "note": "Bảng rỗng (0 rows)", "time_info": ""}
            continue

        ts_cols = [c.strip() for c in ts_cols_str.split(",")]
        try:
            # Bước 1: Quét và đếm sự tồn tại dữ liệu của TẤT CẢ các cột thời gian có trong bảng
            count_selects = ", ".join([f'COUNT("{c}") AS c_{i}' for i, c in enumerate(ts_cols)])
            cur.execute(f'SELECT {count_selects} FROM "{schema}"."{table}"')
            counts = cur.fetchone()
            
            # Chọn ra cột có số lượng dữ liệu COUNT cao nhất (đại diện tính chính xác cao nhất)
            best_col = ts_cols[0]
            max_count = -1
            for i, c in enumerate(ts_cols):
                if counts[f"c_{i}"] > max_count:
                    max_count = counts[f"c_{i}"]
                    best_col = c

            if max_count == 0:
                result[(schema, table)] = {"growth": None, "note": f"Cột [{best_col}] toàn NULL", "time_info": f"{best_col}, start_date: {start_time}" if start_time else f"{best_col}, start_date: "}
                continue
            
            # Bước 2: Rẽ nhánh dựa trên Input của người dùng
            if start_time and end_time:
                # Đếm trực tiếp các dòng thỏa mãn bộ lọc Timeframe
                cur.execute(f'''
                    SELECT COUNT("{best_col}") AS period_count
                    FROM "{schema}"."{table}"
                    WHERE "{best_col}" >= %s AND "{best_col}" <= %s
                ''', (start_time, end_time))
                period_rows = cur.fetchone()["period_count"]
                
                # Tính số tháng theo logic báo cáo tháng để tránh lệch với cách đối chiếu thủ công
                # Ví dụ 2025-01-01 → 2026-05-31 được tính là 17 tháng, không lấy số giây / 30 ngày.
                months = calc_calendar_months_inclusive(start_time, end_time)
                note = f"USER_INPUT({best_col})"
                time_info = f"{best_col}, start_date: {start_time}"
            else:
                # Thuật toán cũ: Nếu không có timeframe, lấy MIN MAX của chính cột tốt nhất
                cur.execute(f'SELECT MIN("{best_col}") AS mn, MAX("{best_col}") AS mx FROM "{schema}"."{table}"')
                row = cur.fetchone()
                mn, mx = row["mn"], row["mx"]
                
                if mn is None or mx is None or mn == mx:
                    result[(schema, table)] = {"growth": None, "note": f"Lỗi MIN=MAX({best_col})", "time_info": f"{best_col}, start_date: {mn}" if mn else f"{best_col}, start_date: "}
                    continue
                
                import datetime as dt
                if hasattr(mx, "timestamp"):
                    months = (mx.timestamp() - mn.timestamp()) / 2_592_000
                else:
                    months = (mx - mn).days / 30.44
                
                period_rows = max_count  # Dùng max_count thay cho tổng row của toàn bảng để tránh sai số dữ liệu cũ
                note = f"MIN→MAX({best_col})"
                time_info = f"{best_col}, start_date: {mn}"
            
            # Output kết quả cuối
            if months < 0.1:
                result[(schema, table)] = {"growth": None, "note": f"Khoảng TG < 0.1 tháng", "time_info": time_info}
            else:
                growth_rate = round(period_rows / months)
                result[(schema, table)] = {"growth": growth_rate, "note": note, "time_info": time_info}
                
        except Exception as e:
            conn.rollback()
            result[(schema, table)] = {"growth": None, "note": "Lỗi truy vấn SQL", "time_info": ""}
            
    cur.close()
    print("✅")
    return result


SQL_COL_STATS = """
SELECT
    s.schemaname                                        AS schema_name,
    s.tablename                                         AS table_name,
    s.attname                                           AS column_name,
    pc.reltuples::bigint                                AS total_rows,
    ROUND((s.null_frac * 100)::numeric, 1)              AS null_pct,
    ROUND((s.null_frac * pc.reltuples)::numeric)        AS null_count_est,
    s.n_distinct,
    CASE
        WHEN s.n_distinct >= 0
             THEN s.n_distinct::bigint
        WHEN pc.reltuples > 0
             THEN ROUND(ABS(s.n_distinct) * pc.reltuples)::bigint
        ELSE NULL
    END                                                 AS cardinality_est,
    CASE WHEN s.histogram_bounds IS NOT NULL
         THEN (s.histogram_bounds::text::text[])[1]
         ELSE NULL
    END                                                 AS col_min,
    CASE WHEN s.histogram_bounds IS NOT NULL
         THEN (s.histogram_bounds::text::text[])
                  [array_length(s.histogram_bounds::text::text[], 1)]
         ELSE NULL
    END                                                 AS col_max,
    CASE WHEN s.most_common_vals IS NOT NULL
         THEN array_to_string(
                  (s.most_common_vals::text::text[])[1:5], ' | ')
         ELSE NULL
    END                                                 AS most_common_vals,
    CASE WHEN s.most_common_freqs IS NOT NULL
         THEN array_to_string(
                  ARRAY(
                      SELECT ROUND((v::numeric)*100, 1)::text || '%'
                      FROM unnest((s.most_common_freqs)[1:5]) v
                  ), ' | ')
         ELSE NULL
    END                                                 AS most_common_freqs,
    COALESCE(st.last_analyze, st.last_autoanalyze)  AS last_analyzed
FROM pg_stats s
JOIN pg_class pc
    ON pc.relname = s.tablename
   AND pc.relnamespace = (
       SELECT oid FROM pg_namespace WHERE nspname = s.schemaname)
LEFT JOIN pg_stat_user_tables st
    ON st.schemaname = s.schemaname
   AND st.relname    = s.tablename
WHERE s.schemaname NOT IN ('pg_catalog','information_schema')
ORDER BY s.schemaname, s.tablename, s.attname;
"""


def get_column_stats(conn, col_rows):
    """
    Đọc toàn bộ pg_stats trong 1 query duy nhất.
    Không scan bảng → nhanh, không tốn I/O, an toàn cho production.
    Số liệu là ước tính dựa trên lần ANALYZE gần nhất.
    """
    print("   Đọc pg_stats (1 query)...", end=" ", flush=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(SQL_COL_STATS)
    rows = cur.fetchall()
    cur.close()
    print(f"✅  {len(rows)} cột")

    pg_map = {
        (r["schema_name"], r["table_name"], r["column_name"]): r
        for r in rows
    }

    stats = {}
    for r in col_rows:
        key    = (r["table_schema"], r["table_name"], r["column_name"])
        s      = pg_map.get(key)
        is_pii = auto_pii(*key)

        if s is None:
            sibling = next(
                (v for k, v in pg_map.items()
                 if k[0] == key[0] and k[1] == key[1]),
                None
            )
            is_empty = sibling is not None and int(sibling.get("total_rows") or 0) == 0
            stats[key] = dict(
                total_rows=0, cardinality=0, distinct_pct="",
                null_count=0, null_pct=0,
                col_min="", col_max="", most_common_freqs="",
                sample="— Bảng rỗng (0 rows)" if is_empty else "⚠️ Không có trong pg_stats",
                last_analyzed="",
            )
            continue

        total_rows  = int(s["total_rows"] or 0)
        cardinality = s["cardinality_est"]
        null_pct    = float(s["null_pct"] or 0)

        distinct_pct = (
            f"{round(cardinality / total_rows * 100, 1)}%"
            if isinstance(cardinality, int) and total_rows > 0 else ""
        )

        raw_sample = s["most_common_vals"] or s["col_min"] or ""
        sample = mask_pii_sample(raw_sample) if (is_pii and raw_sample) else raw_sample

        stats[key] = dict(
            total_rows=total_rows,
            cardinality=cardinality,
            distinct_pct=distinct_pct,
            null_count=s["null_count_est"],
            null_pct=null_pct,
            col_min=s["col_min"] or "",
            col_max=s["col_max"] or "",
            most_common_freqs=s["most_common_freqs"] or "",
            sample=sample,
            last_analyzed=str(s["last_analyzed"]) if s["last_analyzed"] else "",
        )
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# SHEET BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_tables(ws, rows, growth):
    hdrs = ["Schema","Table Name","Phân loại","Comment",
            "Row Count","Columns","Total Size","Data Size","Index Size",
            "Has PK","FK Count",
            "Timestamp Cols","Timestamp sử dụng",
            "Tăng trưởng\n(rows/tháng)","Ghi chú tăng trưởng"]
    style_title(ws, "📋  THÔNG TIN CÁC BẢNG", len(hdrs))
    write_headers(ws, hdrs)
    for ri, r in enumerate(rows, 3):
        cl  = classify_table(r["table_name"])
        bg  = alt(ri)
        key = (r["schema_name"], r["table_name"])
        
        # Lấy dict chứa kết quả trả về từ thuật toán compute_growth mới
        gpm_info = growth.get(key)
        if gpm_info is not None:
            gpm = gpm_info["growth"]
            note = gpm_info["note"]
            time_info = gpm_info.get("time_info", "")
        else:
            gpm = None
            note = "Không có cột timestamp" if not r.get("ts_columns") else "Không đủ dữ liệu"
            time_info = ""

        vals = [
            r["schema_name"], r["table_name"], cl, r["table_comment"] or "",
            r["row_count"], r["column_count"],
            r["total_size"], r["data_size"], r["index_size"],
            "✔" if r["has_pk"] else "✘",
            r["fk_count"],
            r["ts_columns"] or "",
            time_info,
            gpm if gpm is not None else "",
            note,
        ]
        for ci, v in enumerate(vals, 1):
            cell = style_cell(ws.cell(row=ri, column=ci, value=v),
                              bg=bg, center=(ci in (5,6,10,11,14)))
            if ci == 3:
                cell.font = Font(name="Arial", size=10, bold=True,
                                 color=CL_COLORS.get(cl,"595959"))
            if ci == 10:
                cell.font = Font(name="Arial", size=10,
                                 color="375623" if v == "✔" else "C00000")
            if ci == 14 and isinstance(v, (int,float)) and v > 100_000:
                cell.fill = PatternFill("solid", fgColor=C_WARN)
    auto_width(ws)


def build_schemas(ws, rows):
    hdrs = ["Schema Name","Mục đích suy luận","Số bảng","Tổng kích thước","Comment DB"]
    style_title(ws, "🏛️  DANH SÁCH SCHEMA", len(hdrs))
    write_headers(ws, hdrs)
    label_map = {
        "production": "🟢 Production",
        "audit":      "🔵 Audit",
        "archive":    "🟤 Archive",
        "staging":    "🟡 Staging",
        "log":        "🟠 Log",
    }
    for ri, r in enumerate(rows, 3):
        purpose = classify_schema(r["schema_name"])
        for ci, v in enumerate([
            r["schema_name"], label_map.get(purpose,"⚪ Unknown"),
            r["table_count"] or 0, r["total_size"] or "0 bytes",
            r["schema_comment"] or ""
        ], 1):
            style_cell(ws.cell(row=ri, column=ci, value=v),
                       bg=alt(ri), bold=(ci==1), center=(ci==3))
    auto_width(ws)


def build_indexes(ws, rows):
    hdrs = ["Schema","Table","Index Name","Is PK","Is Unique","Definition"]
    style_title(ws, "🔑  DANH SÁCH INDEX", len(hdrs))
    write_headers(ws, hdrs)
    for ri, r in enumerate(rows, 3):
        bg = C_PK if r["is_pk"] == "YES" else alt(ri)
        for ci, v in enumerate([
            r["schema_name"], r["table_name"], r["index_name"],
            r["is_pk"], r["is_unique"], r["index_def"]
        ], 1):
            style_cell(ws.cell(row=ri, column=ci, value=v),
                       bg=bg, center=(ci in (4,5)))
    auto_width(ws)


def build_triggers(ws, rows):
    hdrs = ["Schema","Table","Trigger Name","Timing","Event","Function","Enabled"]
    style_title(ws, "⚡  DANH SÁCH TRIGGER", len(hdrs))
    write_headers(ws, hdrs)
    if not rows:
        ws.cell(row=3, column=1,
                value="Không có trigger nào.").font = Font(
            italic=True, color="888888", name="Arial", size=10)
        return
    for ri, r in enumerate(rows, 3):
        for ci, v in enumerate([
            r["schema_name"], r["table_name"], r["trigger_name"],
            r["timing"], r["event"], r["function_name"], r["enabled"]
        ], 1):
            style_cell(ws.cell(row=ri, column=ci, value=v),
                       bg=alt(ri), center=(ci in (4,5,7)))
    auto_width(ws)


def build_implicit_fk(ws, rows):
    hdrs = ["Schema","Table","Column","Data Type","Likely References Table","Ghi chú"]
    style_title(ws, "🔍  FK NGẦM — Cột *_id / *_code chưa có FK Constraint", len(hdrs))
    write_headers(ws, hdrs)
    if not rows:
        ws.cell(row=3, column=1,
                value="Không phát hiện FK ngầm nào.").font = Font(
            italic=True, color="888888", name="Arial", size=10)
        return
    for ri, r in enumerate(rows, 3):
        for ci, v in enumerate([
            r["table_schema"], r["table_name"], r["column_name"],
            r["data_type"], r["likely_ref_table"],
            "⚠️ Cân nhắc thêm FK constraint"
        ], 1):
            style_cell(ws.cell(row=ri, column=ci, value=v), bg=C_WARN)
    auto_width(ws)


def build_columns(ws, rows):
    hdrs = ["Schema","Table","#","Column Name",
            "Data Type","Full Type","Nullable","Default",
            "PK","FK","UQ","PII/PHI","Comment"]
    style_title(ws, "🔍  CHI TIẾT CÁC CỘT", len(hdrs))
    write_headers(ws, hdrs)
    for ri, r in enumerate(rows, 3):
        is_pk = r["pk"] == "PK"
        is_fk = r["fk"] == "FK"
        pii   = auto_pii(r["table_schema"], r["table_name"], r["column_name"])
        bg    = (C_PII if pii else
                 C_PK  if is_pk else
                 C_FK  if is_fk else alt(ri))
        vals  = [
            r["table_schema"], r["table_name"], r["col_order"], r["column_name"],
            r["data_type"], r["full_type"],
            "YES" if r["is_nullable"]=="YES" else "NO",
            r["column_default"] or "",
            r["pk"], r["fk"], r["uq"],
            "🔴 PII/PHI" if pii else "",
            r["col_comment"] or "",
        ]
        for ci, v in enumerate(vals, 1):
            style_cell(ws.cell(row=ri, column=ci, value=v),
                       bg=bg, bold=(ci==4 and is_pk),
                       center=(ci in (3,7,9,10,11,12)))
    # Legend
    leg = len(rows) + 4
    ws.cell(row=leg, column=1, value="Legend:").font = Font(
        bold=True, size=10, name="Arial")
    for ci, (bg, lbl) in enumerate([
        (C_PII, "🔴 PII/PHI – dữ liệu cá nhân / y tế nhạy cảm (auto-detect)"),
        (C_PK,  "🟢 PK – Primary Key"),
        (C_FK,  "🟡 FK – Foreign Key"),
    ], 2):
        c = ws.cell(row=leg, column=ci, value=lbl)
        c.fill = PatternFill("solid", fgColor=bg)
        c.font = Font(size=9, name="Arial")
    auto_width(ws)


def build_col_stats(ws, col_rows, stats):
    hdrs = ["Schema","Table","Column","Data Type",
            "Total Rows\n(ước tính)","Cardinality\n(ước tính)","Distinct %",
            "Null Count\n(ước tính)","Null %",
            "Min","Max",
            "Most Common Values (≤5, che PII)","Tần suất (%)","PII/PHI",
            "Last Analyzed"]
    style_title(ws,
        "📊  THỐNG KÊ CỘT — pg_stats (ước tính, không scan bảng)",
        len(hdrs))
    write_headers(ws, hdrs)
    for ri, r in enumerate(col_rows, 3):
        key      = (r["table_schema"], r["table_name"], r["column_name"])
        s        = stats.get(key, {})
        pii      = auto_pii(*key)
        null_pct = s.get("null_pct", "")
        bg = C_PII if pii else alt(ri)
        vals = [
            r["table_schema"], r["table_name"], r["column_name"], r["data_type"],
            s.get("total_rows",""), s.get("cardinality",""), s.get("distinct_pct",""),
            s.get("null_count",""),
            f"{null_pct}%" if isinstance(null_pct, (int,float)) else null_pct,
            s.get("col_min",""), s.get("col_max",""),
            s.get("sample",""),
            s.get("most_common_freqs",""),
            "🔴" if pii else "",
            s.get("last_analyzed",""),
        ]
        for ci, v in enumerate(vals, 1):
            cell_bg = (C_WARN
                       if ci == 9
                          and isinstance(null_pct, (int,float))
                          and null_pct > 50
                       else bg)
            cell = style_cell(ws.cell(row=ri, column=ci, value=clean_cell(v)),
                              bg=cell_bg, center=(ci in (5,6,7,8,9,14)))
            if ci in (12, 13):
                cell.alignment = Alignment(vertical="center", wrap_text=True)
    auto_width(ws)
    ws.column_dimensions[get_column_letter(12)].width = 50
    ws.column_dimensions[get_column_letter(13)].width = 30
    ws.column_dimensions[get_column_letter(10)].width = 22
    ws.column_dimensions[get_column_letter(11)].width = 22
    ws.column_dimensions[get_column_letter(15)].width = 22
    # Note
    note_row = len(col_rows) + 4
    note = ws.cell(row=note_row, column=1,
                   value="ℹ️  Dữ liệu từ pg_stats — ước tính dựa trên lần ANALYZE gần nhất. "
                         "Chạy ANALYZE <table> để cập nhật.")
    note.font = Font(name="Arial", size=9, italic=True, color="595959")


def build_relations(ws, rows):
    hdrs = ["Constraint Name",
            "From Schema","From Table","From Column","→",
            "To Schema","To Table","To Column",
            "On Update","On Delete"]
    style_title(ws, "🔗  MỐI QUAN HỆ GIỮA CÁC BẢNG (Foreign Keys)", len(hdrs))
    write_headers(ws, hdrs)
    if not rows:
        ws.cell(row=3, column=1,
                value="Không tìm thấy foreign key nào.").font = Font(
            italic=True, color="888888", name="Arial", size=10)
        return
    for ri, r in enumerate(rows, 3):
        bg   = alt(ri)
        vals = [
            r["constraint_name"],
            r["from_schema"], r["from_table"], r["from_column"], "→",
            r["to_schema"],   r["to_table"],   r["to_column"],
            r["update_rule"], r["delete_rule"],
        ]
        for ci, v in enumerate(vals, 1):
            cell = style_cell(ws.cell(row=ri, column=ci, value=v),
                              bg=bg, center=(ci==5))
            if ci == 5:
                cell.font = Font(name="Arial", size=12, bold=True, color="2E75B6")
            elif ci in (3,7):
                cell.font = Font(name="Arial", size=10, bold=True)
            elif ci in (4,8):
                cell.font = Font(name="Arial", size=10, color="2E75B6")
    auto_width(ws)


def build_info(ws, cfg, counts, generated_at):
    ws["A1"] = "PostgreSQL Database Statistics Report  v3.0  (Y tế / HIS-EMR)"
    ws["A1"].font = Font(name="Arial", bold=True, size=14, color=C_TITLE)
    ws["A1"].fill = PatternFill("solid", fgColor=C_ALT)
    rows = [
        ("Database",                      cfg["dbname"]),
        ("Host",                          f"{cfg['host']}:{cfg['port']}"),
        ("Generated at",                  generated_at),
        ("Domain",                        "Y tế / Bệnh viện (HIS-EMR)"),
        ("", ""),
        ("Schemas",                       counts["schemas"]),
        ("Tables",                        counts["tables"]),
        ("Columns",                       counts["columns"]),
        ("Foreign Keys",                  counts["relations"]),
        ("Implicit FKs (chưa constraint)",counts["impl_fk"]),
        ("PII/PHI Columns (auto-detect)", counts["pii"]),
    ]
    for i, (k, v) in enumerate(rows, 3):
        ws.cell(row=i, column=1, value=k).font = Font(name="Arial", bold=True, size=10)
        ws.cell(row=i, column=2, value=v).font = Font(name="Arial", size=10)
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 40


# ─────────────────────────────────────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("════════════════════════════════════════════════════════════════")
    print("⏳ TÙY CHỌN MỐC THỜI GIAN TÍNH TĂNG TRƯỞNG DỮ LIỆU")
    print("   (Bỏ trống và ấn Enter nếu muốn tự động lấy theo Min-Max bảng)")
    start_time = get_time_input("👉 Nhập mốc bắt đầu (YYYY-MM-DD HH:MM:SS): ")
    end_time = None
    if start_time:
        end_time = get_time_input("👉 Nhập mốc kết thúc (YYYY-MM-DD HH:MM:SS): ", is_end=True)
        if not end_time:
            print("⚠️  Đã nhập start_time nhưng bỏ trống end_time → KHÔNG dùng timeframe input, quay về Min-Max mặc định.")
            start_time, end_time = None, None
        elif end_time <= start_time:
            print("❌ Lỗi: Thời gian kết thúc phải lớn hơn thời gian bắt đầu. Thuật toán sẽ quay về dùng Min-Max mặc định.")
            start_time, end_time = None, None

    print("\n🎯 TÙY CHỌN BẢNG CẦN TỔNG HỢP")
    print("   Nhập dạng: A, B hoặc schema.A, schema.B")
    print("   Bỏ trống và ấn Enter nếu muốn duyệt toàn bộ bảng trong database")
    selected_tables = get_table_input("👉 Nhập danh sách bảng cần tổng hợp: ")

    cfg       = DB_CONFIG
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output    = OUTPUT_FILE or \
        f"pg_stats_{cfg['dbname']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    print(f"\n🔌  Kết nối tới {cfg['host']}:{cfg['port']}/{cfg['dbname']} ...")
    try:
        conn = psycopg2.connect(**cfg, connect_timeout=10)
        conn.set_session(readonly=True, autocommit=True)
    except Exception as e:
        print(f"❌  Kết nối thất bại: {e}", file=sys.stderr)
        sys.exit(1)

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("📥  Truy vấn metadata...")
    cur.execute(SQL_SCHEMAS);     schemas   = cur.fetchall()
    cur.execute(SQL_TABLES);      tables    = cur.fetchall()
    cur.execute(SQL_IMPLICIT_FK); impl_fk   = cur.fetchall()
    cur.execute(SQL_COLUMNS);     columns   = cur.fetchall()
    cur.execute(SQL_RELATIONS);   relations = cur.fetchall()
    cur.close()

    schemas, tables, impl_fk, columns, relations = filter_report_data_by_tables(
        schemas, tables, impl_fk, columns, relations, selected_tables
    )

    pii_count = sum(
        1 for r in columns
        if auto_pii(r["table_schema"], r["table_name"], r["column_name"])
    )
    print(f"   ✅  {len(schemas)} schema | {len(tables)} bảng | {len(columns)} cột")
    print(f"       {len(impl_fk)} FK ngầm | {len(relations)} FK | {pii_count} PII/PHI col")

    print("\n📈  Tính tốc độ tăng trưởng (quét độ hoàn chỉnh cột)...")
    growth = compute_growth(conn, tables, start_time, end_time)

    print("\n📊  Đọc thống kê cột từ pg_stats...")
    col_stats = get_column_stats(conn, columns)
    conn.close()

    print("\n📝  Tạo file Excel...")
    wb  = Workbook()
    ws1 = wb.active;               ws1.title = "1. Thông tin bảng"
    ws2 = wb.create_sheet("2. Schema")
    ws3 = wb.create_sheet("3. FK ngầm")
    ws4 = wb.create_sheet("4. Chi tiết cột")
    ws5 = wb.create_sheet("5. Thống kê cột")
    ws6 = wb.create_sheet("6. Quan hệ bảng")
    wsi = wb.create_sheet("Info")

    build_tables(ws1, tables, growth)
    build_schemas(ws2, schemas)
    build_implicit_fk(ws3, impl_fk)
    build_columns(ws4, columns)
    build_col_stats(ws5, columns, col_stats)
    build_relations(ws6, relations)
    build_info(wsi, cfg, dict(
        schemas=len(schemas), tables=len(tables), columns=len(columns),
        relations=len(relations), impl_fk=len(impl_fk), pii=pii_count,
    ), generated)

    wb.save(output)
    print(f"\n✅  Đã lưu: {output}")
    print("   Sheet 1 – Thông tin bảng    (phân loại, tăng trưởng = rows ÷ tháng[MIN→MAX ts])")
    print("   Sheet 2 – Schema            (mục đích suy luận)")
    print("   Sheet 3 – FK ngầm           (*_id/*_code chưa có constraint)")
    print("   Sheet 4 – Chi tiết cột      (PII/PHI auto-detect)")
    print("   Sheet 5 – Thống kê cột      (pg_stats: distinct%, null%, min/max, most_common_vals)")
    print("   Sheet 6 – Quan hệ bảng      (FK chính thức)")
    print("   Info    – Metadata báo cáo")


if __name__ == "__main__":
    main()