# Codebase Index

## 1. Mục đích

Đây là bộ khung kiểm tra chất lượng dữ liệu theo các dimensions của Data Quality. Ứng dụng kết nối tới PostgreSQL, tải metadata và các thống kê tiền phân tích, lưu cache để tái sử dụng, sau đó các dimension dùng dữ liệu này để thực hiện các kiểm tra riêng.

## 2. Cấu trúc tổng quan

```text
.
├── core/                    # Hạ tầng dùng chung: cấu hình và kết nối database
├── data_quality/            # Các dimensions và luồng tải metadata
│   ├── completeness/        # Đầy đủ dữ liệu
│   ├── consistency/         # Nhất quán dữ liệu
│   ├── uniqueness/          # Tính duy nhất
│   ├── accuracy/            # Tính chính xác (dự kiến)
│   ├── timeliness/          # Tính kịp thời (dự kiến)
│   ├── validity/            # Tính hợp lệ (dự kiến)
│   ├── load/                # Các loader schema/table/column/statistics
│   ├── load_metadata.py     # Điểm tổng hợp metadata và đồng bộ Excel
│   ├── load_completeness.py # Đồng bộ kết quả completeness từ cache vào Excel
│   └── load_consistency.py  # Orchestrate consistency steps từ metadata và business rules
├── utils/                   # Các tiện ích dùng chung
├── cache/                   # Cache metadata theo schema và kết quả theo database
├── confirmed_business/      # Quy tắc nghiệp vụ đã xác nhận, dạng YAML
├── docs/                    # Tài liệu dự án
└── read-only/               # Mã legacy/tham khảo, không thuộc luồng chính
```

Các thư mục `accuracy`, `timeliness` và `validity` hiện là định hướng kiến trúc; chỉ tạo module khi có yêu cầu kiểm tra cụ thể.

### Tài liệu chuẩn các dimensions

#### `read-only/bao_cao_chat_luong_du_lieu.docx.pdf`

Tài liệu chuẩn hóa mục tiêu, các bước thực hiện và kết quả đầu ra của các chiều chất lượng dữ liệu cho hệ thống HIS. Tài liệu hiện bao gồm:

- `Completeness`: tổng quan database, phân tầng bảng, đo NULL, phân loại `Meaningful NULL` và phát hiện placeholder.
- `Consistency`: xác định chuỗi quan hệ bảng, kiểm tra orphan record, pattern logic, enum và mâu thuẫn cross-table.
- `Uniqueness`: kiểm tra trùng kỹ thuật, trùng nghiệp vụ và UNIQUE constraint.
- `Accuracy`, `Timeliness`, `Validity`: phạm vi kiểm tra, giới hạn và các bước đánh giá tương ứng.
- `Integrity`, `Temporal Consistency` và `Lineage`: các chiều mở rộng cho chuỗi quan hệ, tính nhất quán theo thời gian và truy xuất nguồn gốc.

Đây là tài liệu tham chiếu nghiệp vụ khi triển khai hoặc đánh giá phạm vi của từng dimension; code hiện tại có thể mới chỉ bao phủ một phần các bước trong tài liệu.

#### `docs/DATA_QUALITY_ANALYSIS_GUIDE.md`

Hướng dẫn thực hiện chính thức theo từng bước cho từng dimension. Tài liệu ghi rõ điều kiện phụ thuộc, cách chạy, đầu ra, phần cần xác nhận với nghiệp vụ và phạm vi implementation hiện tại.

## 3. `core/` — hạ tầng nền tảng

### `core/settings.py`

Định nghĩa `Config` và đối tượng cấu hình dùng chung `config`.

- Đọc thông tin kết nối PostgreSQL từ `.env` thông qua các biến `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- Xác định `base_dir` và `cache_dir`.
- Sinh đường dẫn cache của từng schema bằng `schema_cache_file(schema)`.
- Sinh đường dẫn file Excel kết quả của database bằng `db_result_file`.

Đây là nơi duy nhất nên tập trung các đường dẫn và thông số môi trường; code nghiệp vụ không nên tự ghép đường dẫn hoặc đọc `.env` trực tiếp.

### `core/database.py`

Cung cấp context manager `get_connection(read_only=True)`.

- Tạo DuckDB in-memory làm lớp truy vấn trung gian.
- Attach PostgreSQL dưới tên `remote_db` rồi chuyển context sang database này.
- Mặc định mở read-only.
- `load_table.py` dùng `read_only=False` vì có thể chạy `ANALYZE` để cập nhật statistics.
- Luôn đóng connection khi kết thúc context.

### `core/logging.py`

Cung cấp dimension logger (`setup_dimension_logger` hoặc alias
`setup_logger`) và step logger (`setup_step_logger`). Dimension log có format
`[Dimension] message`; step log được thụt đầu dòng một tab với format
`\t[Step N] message`. Step phải bám theo các bước đã nêu trong
`docs/DATA_QUALITY_ANALYSIS_GUIDE.md`; logger không ghi thông tin kết nối, SQL
hoặc dữ liệu nhạy cảm.

## 4. `data_quality/` — các dimensions

Mỗi folder con tương ứng với một dimension của Data Quality Check:

1. `completeness` — kiểm tra dữ liệu có đầy đủ hay không, ví dụ thiếu dòng hoặc giá trị NULL.
2. `consistency` — kiểm tra sự nhất quán giữa các bảng, cột hoặc quy tắc liên quan.
3. `uniqueness` — kiểm tra khóa, bản ghi hoặc giá trị bị trùng.
4. `accuracy` — kiểm tra dữ liệu có phản ánh đúng giá trị kỳ vọng hay không.
5. `timeliness` — kiểm tra dữ liệu có được cập nhật đúng thời gian yêu cầu hay không.
6. `validity` — kiểm tra dữ liệu có đúng kiểu, miền giá trị và format hợp lệ hay không.

Quy ước của dimension:

- Hàm xử lý riêng chỉ load/tính toán và trả về dữ liệu kết quả.
- Không tự mở, sửa hoặc lưu file Excel.
- Việc tổng hợp metadata và reconciliation thuộc nội bộ file điều phối cấp cao như `load_metadata.py`; `utils/excel.py` chỉ cung cấp các thao tác workbook/sheet dùng chung.

### Hiện trạng completeness

#### `data_quality/completeness/step1_table_row_count.py`

Cung cấp `load_table_row_count(schema)`, đọc metadata của schema từ cache và
trả về danh sách theo từng bảng với các trường thống kê tương ứng. Dependency
được kiểm tra ở orchestrator, không tạo hoặc sửa file rule trong Step 1.

#### `data_quality/completeness/step2_table_classification.py`

Cung cấp `prepare_table_tiers(rows)` để tạo/cập nhật
`confirmed_business/completeness/table_tiers.yaml`: Tier 2/3 được gợi ý tự
động theo keyword trong `confirmed_business/tier_constraints.yaml`; Tier 1 và
bảng không khớp được tạo với `tier:` trống và `status: need_check`. Sau khi
người dùng điền Tier 1/2/3, `check_table_tiers_complete(rows)` xác nhận đủ
đầu vào, rồi `classify_tables(rows)` mới load các rule đã hoàn tất.
Step 2 không còn yêu cầu dependency business bắt buộc.

#### `data_quality/load_completeness.py`

Cung cấp `load_completeness()`, đọc cache metadata, lấy phân loại bảng từ
confirmed business YAML và tạo/cập nhật sheet `Tables` và `Columns`. Sheet
không còn là nguồn xác nhận và classification không được ghi ngược vào cache.
Luồng column đã được triển khai, nhưng chưa bao phủ đầy đủ bước `Meaningful
NULL` theo tài liệu chuẩn.

#### `data_quality/completeness/step3_col_null_count.py`

Cung cấp `load_column_null_count(schema)` để đọc và trả về tỷ lệ NULL từ
statistics. Việc gán nhãn và tô màu `Đỏ/Vàng/Xanh` thuộc luồng build Excel
trong `load_completeness.py`, không thuộc module tính toán Step 3.

#### `data_quality/completeness/step4_col_null_classification.py`

Orchestrator kiểm tra hai rule nghiệp vụ của Step 4:
`col_notnullable.yaml` và `col_null_classified.yaml`; Step 4 chỉ được gọi khi
cả hai file có ít nhất một rule `status: confirmed`. Hàm
`classify_columns()` chỉ đọc các rule đã xác nhận và bổ sung `NotNullable` cùng
`Null Classify` vào dữ liệu cột; việc ghi Excel thuộc luồng điều phối
`load_completeness.py`.

`load_completeness()` sau khi có phân loại bảng sẽ bổ sung cảnh báo `Null
Warning`, cùng `NotNullable` và `Null Classify` vào dữ liệu cột;
`utils/excel.py` khai báo các cột tương ứng trong sheet `Columns`. `Null
Warning` dùng Tier của bảng và `Null pct`: Tier 1 vượt 5% là `🔴 Nguy hiểm`,
Tier khác 1 vượt 90% là `🟡 Nghi ngờ`, còn lại là `🟢 Bình thường`.
`NotNullable` và `Null Classify` chỉ được điền từ rule nghiệp vụ có
`status: confirmed`.

Phạm vi hiện tại của bước phân loại NULL:

- Đã đo tỷ lệ NULL theo cột từ statistics trong cache.
- Đã có cảnh báo sơ bộ theo ngưỡng `> 5%` và `> 90%`.
- Chưa xác định cột bắt buộc theo nghiệp vụ; Tier 1 hiện đang được dùng làm proxy cho cảnh báo `> 5%`.
- Chưa phân tích cột điều kiện để phân biệt `Meaningful NULL`, `Orphan NULL` và `Lazy NULL`.
- Vì vậy, kết quả hiện tại chưa phải là kết luận cuối cùng về NULL; các cột cảnh báo cần được đối chiếu với nghiệp vụ.

#### `data_quality/completeness/step5_dirty_value_check.py`

Cung cấp `load_dirty_value_check(schema)`, đọc danh sách bảng/cột từ cache
nhưng truy vấn trực tiếp database ở chế độ read-only để tìm các giá trị
placeholder hoặc mặc định thường gặp thay cho `NULL`. Kết quả được ghi vào
cột `Dirty Check` khi chạy `load_completeness` dưới dạng `Pass` hoặc
`Failed: value (pct%)`, với tỷ lệ tính trên tổng số dòng của bảng.

Hiện đã kiểm tra chuỗi rỗng, một số chuỗi placeholder (`N/A`, `.`, `-`, `_`, `unknown`, `x`), các giá trị số `0`, `-1`, `9999` và ngày mặc định bắt đầu bằng `1900-01-01` hoặc `1970-01-01`. Danh sách này cần bổ sung/điều chỉnh theo quy ước thực tế của từng hệ thống.

## 5. `data_quality/load/` — metadata và tiền phân tích

Nhóm loader này không phải một dimension; nó cung cấp dữ liệu nền cho tất cả dimensions.

### `data_quality/load/load_schema.py`

- `load_schemas()` lấy danh sách schema, loại trừ schema hệ thống và schema tạm.
- `load_schema_summary(schema)` lấy số bảng, tổng kích thước và comment của schema để dùng cho sheet `Schemas`.
- `load_schema(schema, max_age_days=5, refresh=False)` đọc cache JSON nếu đã có; nếu chưa có hoặc được yêu cầu refresh thì gọi loader bảng/cột, sau đó lưu cache.
- `save_schema(schema, data)` ghi cache JSON và chuyển các giá trị ngày giờ sang ISO-8601 để có thể serialize.

### `data_quality/load/load_table.py`

- `load_tables(schema, max_age_days=5)` lấy metadata của các base table: tên, comment, row count, column count, kích thước, khóa và loại bảng.
- `_load_statistics(...)` đọc thời điểm `ANALYZE`/`AUTOANALYZE`, tính tuổi statistics và chạy `ANALYZE` khi quá cũ.
- Statistics của bảng được gắn vào key `statistics` trong metadata trả về.

### `data_quality/load/load_column.py`

- `load_table_with_columns(schema, table)` lấy danh sách cột, kiểu dữ liệu, nullable, default và ordinal position.
- `_load_column_statistics(...)` lấy tổng số dòng và tỷ lệ NULL từ `pg_stats`,
  đồng thời đếm số giá trị distinct bằng `COUNT(DISTINCT ...)`.
- Statistics của cột được gắn vào key `statistics` của từng cột.

### `data_quality/load_metadata.py`

Đây là entry point tổng hợp metadata:

1. Đọc cache schema nếu cache đã tồn tại; cache thiếu thì thực hiện lại luồng load schema/table/column/statistics.
2. Lấy summary hiện tại của từng schema sau khi load metadata.
3. Đọc workbook kết quả hiện có và đối soát dữ liệu schema theo tên schema.
4. Tạo hoặc cập nhật sheet `Schemas` với các cột `Schema`, `Table Count`, `Total Size`, `DB Comment`.
5. Chỉ cập nhật dòng khi dữ liệu lệch và append schema chưa có; không xóa các dòng cũ.
6. Lưu workbook một lần sau khi tổng hợp xong.

Lệnh chạy dự kiến:

```bash
python -m data_quality.load_metadata
```

## 6. `cache/` — lớp lưu trữ trung gian

Cache dùng để lưu kết quả tiền phân tích, gồm:

- metadata của từng schema;
- metadata của các bảng trong schema;
- metadata của các cột trong bảng;
- statistics của bảng và cột tương ứng.

Mỗi schema được lưu thành một file JSON riêng, sinh qua `config.schema_cache_file(schema)`. File Excel kết quả được sinh theo tên database qua `config.db_result_file`.

Cache là nguồn dữ liệu tái sử dụng giữa các lần chạy. Khi cần buộc cập nhật statistics hoặc metadata, dùng tham số `refresh` ở loader cấp schema thay vì tự xóa file cache.

## 6.1. `data_quality/consistency/` — Tính nhất quán

### `data_quality/consistency/step1_table_relationship.py`

Cung cấp `load_table_relationships()`, đọc phân loại `Tier 1` đã xác nhận từ
`confirmed_business/completeness/table_tiers.yaml`, lấy FK chính thức từ PostgreSQL và nối tới PK/UK được
tham chiếu. Kết quả là các cạnh trực tiếp `child → parent`, bao gồm khóa ghép
và quy tắc update/delete; chỉ giữ cạnh khi cả hai bảng đều là Tier 1. Module
này không dựng chain nhiều mắt xích và được tách riêng để module chain sử dụng
ở bước sau.

### `data_quality/consistency/step2_orphan_record.py`

Cung cấp `load_orphan_records(relationships)`, nhận các cạnh FK–PK/UK từ
`step1_table_relationship.py` để kiểm tra từng constraint. Kết quả gồm bảng con, cột
FK, bảng cha, số bản ghi được kiểm tra, số orphan và tỷ lệ orphan. FK nullable
được loại khỏi mẫu kiểm tra; khóa ghép được kiểm tra như một constraint duy nhất.

### `data_quality/consistency/step3_pattern_check.py`

Cung cấp convention chuẩn bị và đo rule cho Pattern Consistency trong
`confirmed_business/consistency/pattern_checks.yaml`. Pattern ngày tháng và
trạng thái được gợi ý tự động theo tên/kiểu cột; pattern tổng–chi tiết được tạo
ở trạng thái `need_check` để data owner xác nhận. Lớp query kiểm tra thực tế
chỉ được đọc các rule có `status: confirmed`, trả về số dòng kiểm tra, số dòng
vi phạm và tỷ lệ vi phạm.

### `data_quality/consistency/step4_enum_check.py`

Cung cấp convention tự nhận diện cột enum theo tên, tạo rule `need_check` trong
`confirmed_business/consistency/enum_checks.yaml`, sau đó đo số giá trị ngoài
`allowed_values` của các rule đã `confirmed`. Kết quả gồm số dòng kiểm tra, số
dòng vi phạm, số giá trị khác nhau vi phạm và tỷ lệ vi phạm.

### `data_quality/consistency/step5_cross_table_check.py`

Cung cấp convention auto-detect thuộc tính lưu lặp trên các quan hệ từ Bước 1,
tạo rule `need_check` trong `confirmed_business/consistency/cross_table_checks.yaml`
và đo các rule `duplicated_value` đã `confirmed`. Kết quả gồm số dòng kiểm tra,
số dòng vi phạm và tỷ lệ vi phạm; `null_policy` mặc định loại dòng có một
trong hai giá trị là `NULL`.

### `data_quality/load_consistency.py`

Cung cấp `load_consistency()`, chạy tuần tự Consistency Bước 1–5. Orchestrator
kiểm tra dependency/rule trước mỗi bước, truyền quan hệ từ Bước 1 sang Bước 2,
dùng metadata column để tạo ứng viên cho Bước 3–5 và trả về kết quả tổng hợp.
Kết quả được ghi vào workbook riêng `{DB_NAME}_consistency.xlsx` với các sheet
Step 2 đến Step 5; Step 1 không tạo sheet.

### Hiện trạng uniqueness

#### `data_quality/uniqueness/step2_technical_duplicate.py`

Cung cấp Layer 1 và Layer 2 của kiểm tra trùng kỹ thuật. Layer 1 thực hiện
phạm vi của Bước 1 bằng cách chỉ giữ bảng Tier 1 và loại trừ bảng log, audit,
history, cấu hình và danh mục. Layer 2 nhóm toàn bộ cột nghiệp vụ còn lại,
loại ID kỹ thuật và timestamp, sau đó trả về số nhóm trùng, số record ảnh
hưởng và tỷ lệ trên số record được kiểm tra.

#### `data_quality/load_uniqueness.py`

Cung cấp `load_uniqueness()`, đọc metadata cache, chạy Layer 1–2 và Bước 4 rồi ghi kết
quả vào workbook riêng `{DB_NAME}_uniqueness.xlsx`. Bước 1 không có lượt quét
database riêng vì đã được thực hiện trong Layer 1 của Bước 2.

#### `data_quality/uniqueness/step4_unique_constraint.py`

Cung cấp `load_unique_constraints()` để lấy các UNIQUE constraint từ PostgreSQL,
gom đúng các khóa ghép, sau đó `load_unique_constraint_violations()` nhóm lại
dữ liệu hiện tại để đếm nhóm trùng, record ảnh hưởng và tỷ lệ duplicate. Phạm
vi dùng cùng bảng Tier 1 và bộ loại trừ của Layer 1.

## 7. `utils/` — tiện ích dùng chung

### `confirmed_business/`

Lưu cấu hình nghiệp vụ dạng YAML theo dimension và bước kiểm tra. Các file hiện
là khung `draft`, chờ data owner điền và xác nhận; không chứa kết quả kiểm tra.

### `utils/confirmed_business.py`

Cung cấp `load_rules(relative_path)` để các dimension đọc YAML bằng
`yaml.safe_load`, kiểm tra đường dẫn và bảo đảm file nằm trong
`confirmed_business/`.

### `utils/excel.py`

Quản lý workbook và sheets bằng `openpyxl`.

- `setup_table_sheet()` mở hoặc tạo workbook, thiết lập sheet `Tables`, header, freeze panes, filter và bảng tra cứu phân loại Tier.
- `setup_column_sheet(workbook)` tạo hoặc mở sheet `Columns` và thiết lập header, freeze panes và filter.
- `load_or_create_workbook()` mở workbook kết quả hiện có hoặc tạo workbook mới.
- `get_or_create_sheet(workbook, name)` lấy sheet theo tên hoặc tạo sheet mới, đồng thời tái sử dụng sheet mặc định rỗng khi phù hợp.
- `setup_schema_sheet(workbook)` lấy hoặc tạo sheet `Schemas`, chuẩn hóa cấu trúc và trả về dữ liệu hiện có; sheet mới trả về dữ liệu rỗng.
- `save_excel(workbook)` lưu workbook về file kết quả của database.
- `load_or_create_consistency_workbook()` mở hoặc tạo workbook riêng cho Consistency.
- `setup_consistency_sheet(workbook, name, headers, rows)` reset và ghi một sheet kết quả của từng step.
- `save_consistency_excel(workbook)` lưu workbook Consistency theo tên `{DB_NAME}_consistency.xlsx`.
- `load_or_create_uniqueness_workbook()`, `setup_uniqueness_sheet()` và
  `save_uniqueness_excel()` quản lý workbook Uniqueness theo tên
  `{DB_NAME}_uniqueness.xlsx`.
- `load_or_create_uniqueness_workbook()`, `setup_uniqueness_sheet()` và
  `save_uniqueness_excel()` quản lý workbook Uniqueness theo tên
  `{DB_NAME}_uniqueness.xlsx`.

Các file dimension và loader không nên tự tạo workbook mới hoặc tự ghép đường dẫn Excel; hãy dùng utility này hoặc mở rộng utility này khi cần thêm sheet dùng chung. Logic reconciliation dữ liệu cụ thể vẫn thuộc file điều phối tương ứng, như `_reconcile_schemas()` trong `load_metadata.py`.

### `utils/formatter.py`

- `quote_identifier(identifier)` escape và quote an toàn tên schema/table/column khi đưa vào SQL động.
- `parse_table_reference(value)` đọc định danh gọn `schema.table` trong business YAML.
- `parse_column_reference(value)` đọc định danh gọn `schema.table.column` trong business YAML.
- Là nơi tập trung các formatter dùng để thống nhất cách hiển thị datatype, kích thước và các kiểu dữ liệu khác khi những formatter đó được bổ sung.

## 8. `read-only/`

### `read-only/raw.py`

Một chương trình legacy/tham khảo chứa truy vấn tổng hợp, logic phân loại và cách dựng nhiều sheet báo cáo. Không xem đây là entry point chính của kiến trúc mới. Khi cần tái sử dụng logic, nên tách thành module phù hợp trong `core`, `utils`, `data_quality/load` hoặc dimension tương ứng.

## 9. Luồng dữ liệu chuẩn

```text
PostgreSQL
    │
    ▼
core.database.get_connection()
    │
    ▼
load_schema ──► load_table ──► load_column
    │                │              │
    │                └── table statistics
    │                               └── column statistics
    ▼
cache/<schema>.json
    │
    ▼
dimension loaders trả về kết quả
    │
    ▼
file tổng hợp cấp cao đối soát với Excel
```

Nguyên tắc chính là tách ba trách nhiệm: loader đọc dữ liệu, dimension tính chất lượng, còn file tổng hợp quản lý workbook và lưu kết quả.
