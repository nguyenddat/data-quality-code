# Hướng dẫn phân tích chất lượng dữ liệu

Tài liệu này là hướng dẫn thực hiện chính thức theo từng bước cho các dimensions trong `read-only/bao_cao_chat_luong_du_lieu.docx.pdf`. Tài liệu phân biệt rõ:

- bước đã được code hóa;
- bước cần xác nhận với nghiệp vụ;
- bước chưa có implementation và phải thực hiện bằng query/script riêng.

## 1. Nguyên tắc chung

### 1.1. Phạm vi

Ưu tiên phân tích theo bảng Tier 1. Tier 2 chỉ kiểm tra chọn lọc hoặc tổng hợp; Tier 3 thường bỏ qua, trừ khi có yêu cầu đặc biệt.

### 1.2. Điều kiện đầu vào

Trước khi chạy dimension, cần có:

1. Kết nối PostgreSQL trong `.env`.
2. Metadata schema/table/column trong `cache/`.
3. Phân loại bảng từ gợi ý tự động của Step 2 và/hoặc rule đã xác nhận trong
   `confirmed_business/completeness/table_tiers.yaml`.
4. Xác nhận với nghiệp vụ đối với các quy tắc không thể suy ra từ database.

Không ghi thông tin bí mật trong log hoặc báo cáo.

### 1.3. Lệnh nền tảng hiện có

Các lệnh Python phải chạy bằng environment `learn_data_engineer`:

```bash
conda run -n learn_data_engineer python -m data_quality.load_metadata
conda run -n learn_data_engineer python -m data_quality.load_completeness
```

`load_metadata` tải metadata và statistics vào cache. `load_completeness` đọc cache, cập nhật sheet `Tables` và `Columns` trong workbook kết quả.

Các dimension chưa có entry point chính thức phải được triển khai thành module riêng trước khi tự động hóa. Query kiểm tra database phải dùng kết nối read-only nếu không cần `ANALYZE`.

## 2. Completeness — Tính đầy đủ

**Mục tiêu:** xác định dữ liệu bị thiếu ở đâu, mức độ nào và NULL đó có hợp lệ theo nghiệp vụ hay không.

### Bước 1 — Lấy bức tranh tổng thể database

**Điều kiện:** không yêu cầu phân loại Tier.

**Cách thực hiện:** chạy `load_metadata` để lấy danh sách bảng, số dòng, số cột, kích thước và thông tin khóa.

**Đánh giá:** lập danh sách bảng rỗng, bảng ít dữ liệu và bảng có dữ liệu. Bảng rỗng phải được hỏi nghiệp vụ trước khi kết luận là vấn đề.

**Hiện trạng code:** đã có metadata và `row_count`; chưa có bước tự động phân nhóm “rỗng/ít/lớn” theo ngưỡng nghiệp vụ.

### Bước 2 — Phân tầng bảng

**Điều kiện:** cần thống nhất tiêu chí Tier với nghiệp vụ/data owner; rule
confirmed thủ công được ưu tiên hơn gợi ý tự động.

**Cách thực hiện:** ghi từng bảng vào `confirmed_business/completeness/table_tiers.yaml`,
đặt `status: confirmed` và `tier` là `1`, `2` hoặc `3`. Sheet `Tables` chỉ là
đầu ra báo cáo và được cập nhật từ YAML khi chạy completeness.

**Đầu ra:** danh sách bảng với Tier 2/3 tự động gợi ý theo tên bảng; Tier 1 và
bảng không khớp rule được gắn `need_check` để data owner xác nhận trong YAML.

### Bước 3 — Đo và sàng lọc tỷ lệ NULL

**Điều kiện:** đã có metadata column và statistics sau Bước 1.

**Cách thực hiện:** chạy `load_completeness`. Tỷ lệ NULL lấy từ `pg_stats` và được ghi vào `Columns.Null pct`.

**Sàng lọc hiện tại:**

- Tier 1 có `null_pct > 5%`: cảnh báo đỏ.
- Tier khác có `null_pct > 90%`: cảnh báo vàng.
- Trường hợp còn lại: màu xanh.

Đây là bước phát hiện ứng viên, chưa phải kết luận lỗi.

### Bước 4 — Phân loại Meaningful NULL

**Điều kiện:** cần danh sách cột cảnh báo từ Bước 3 và quy tắc nghiệp vụ.

**Cách thực hiện:** với mỗi cột NULL cao, xác định cột điều kiện như `trang_thai`, `tinh_trang`, `loai_dich_vu`; sau đó phân phối giá trị điều kiện trên các bản ghi có cột mục tiêu NULL.

Ví dụ:

```sql
SELECT tinh_trang, COUNT(*) AS null_count
FROM benh_nhan
WHERE ngay_tu_vong IS NULL
GROUP BY tinh_trang
ORDER BY null_count DESC;
```

**Phân loại đầu ra:** `Valid NULL`, `Conditional NULL`, `Invalid NULL` hoặc `Needs business confirmation`.

**Hiện trạng code:** chưa tự động hóa.

### Bước 5 — Phát hiện placeholder thay cho NULL

**Điều kiện:** đã có danh sách bảng/cột từ cache.

**Cách thực hiện:** `load_completeness` gọi `load_dirty_value_check` bằng truy vấn read-only.

**Giá trị hiện kiểm tra:** chuỗi rỗng, `N/A`, `.`, `-`, `_`, `unknown`, `x`; số `0`, `-1`, `9999`; ngày bắt đầu bằng `1900-01-01` hoặc `1970-01-01`.

**Đầu ra:** cột `Dirty Check` với `Pass` nếu không phát hiện hoặc
`Failed: value (pct%)` khi phát hiện placeholder.

**Lưu ý:** danh sách placeholder phải được bổ sung theo quy ước của từng hệ thống.

## 3. Consistency — Tính nhất quán

**Mục tiêu:** xác định dữ liệu có mâu thuẫn giữa các bảng, trong cùng bảng hoặc với quy tắc nghiệp vụ.

### Bước 1 — Xác định chuỗi quan hệ bảng

**Điều kiện:** Bước 2 của Completeness đã có Tier; metadata đã được tải.

**Cách thực hiện:** lấy FK chính thức từ PostgreSQL, nối với PK/UK của bảng cha, sau đó chỉ giữ các cạnh mà bảng nguồn và bảng đích thuộc Tier 1. Dựng graph dạng `child → parent` và liệt kê các chuỗi quan hệ.

**Đầu ra:** sơ đồ hoặc bảng gồm `from_table`, `from_column`, `to_table`, `to_column`, constraint và chain.

**Hiện trạng code:** `data_quality/consistency/step1_table_relationship.py` đã có
module đọc phân loại Tier 1 đã xác nhận từ
`confirmed_business/completeness/table_tiers.yaml`, lấy FK
chính thức và PK/UK được tham chiếu, sau đó trả về các cạnh trực tiếp
`child → parent`. Việc dựng chuỗi nhiều mắt xích sẽ được lưu và xử lý ở module
chain riêng.

Luồng chạy tổng hợp dùng `data_quality.load_consistency`; orchestrator dừng ở
bước đầu tiên còn thiếu dependency hoặc business rule `confirmed` và ghi
ứng viên `need_check` vào file rule tương ứng.

### Bước 2 — Kiểm tra orphan record

**Điều kiện:** đã có quan hệ FK–PK từ Bước 1. Phải biết FK nào bắt buộc và FK nào tùy chọn.

**Cách thực hiện:** với mỗi FK, đếm bản ghi con có FK khác NULL nhưng không tìm thấy PK tương ứng ở bảng cha.

```sql
SELECT COUNT(*)
FROM child c
LEFT JOIN parent p ON p.id = c.parent_id
WHERE c.parent_id IS NOT NULL
  AND p.id IS NULL;
```

**Đầu ra:** số orphan và tỷ lệ orphan trên tổng số bản ghi có FK.

**Hiện trạng code:** `data_quality/consistency/step2_orphan_record.py` nhận
quan hệ từ Bước 1 và kiểm tra theo từng constraint. Mẫu tính tỷ lệ gồm các bản ghi có đầy
đủ giá trị FK không NULL; khóa ghép được kiểm tra đồng thời trên toàn bộ cặp
cột. Kết quả gồm `child_table`, `fk_column`, `parent_table`, `orphan_count` và
`orphan_pct`.

### Bước 3 — Kiểm tra pattern logic

**Điều kiện:** nghiệp vụ phải xác nhận cặp cột và quy tắc, không tự suy ra chỉ từ tên cột.

**Cách thực hiện:** áp dụng query theo pattern: ngày bắt đầu trước ngày kết thúc; tổng header bằng tổng line-item; record đã đóng không có giao dịch mới.

**Đầu ra:** rule, số bản ghi vi phạm, tỷ lệ và mức độ ảnh hưởng.

**Convention hiện tại:** `data_quality/consistency/step3_pattern_check.py` tạo
rule gợi ý trong `confirmed_business/consistency/pattern_checks.yaml`. Pattern
ngày tháng và trạng thái được nhận diện tự động nhưng cần data owner cập nhật
field cụ thể rồi chuyển sang `confirmed`; pattern tổng–chi tiết luôn được tạo
ở trạng thái `need_check` để xác nhận bảng, cột tổng, cột chi tiết, khóa nối và
dung sai. Rule trạng thái cần thêm `entity_columns`, `event_time_column`,
`terminal_values` và `require_terminal` để xác định trạng thái cuối cùng có bắt
buộc hay không. Lớp query chỉ sử dụng rule có `status: confirmed`.

`load_pattern_violations()` trả về `checked_count`, `violation_count` và
`violation_pct` cho từng rule. Pattern ngày tháng đếm dòng có `start >= end`;
pattern tổng–chi tiết so sánh tổng header với `SUM` line-item; pattern trạng
thái kiểm tra trạng thái mới nhất của từng thực thể có thuộc tập terminal khi
`require_terminal: true`.

### Bước 4 — Kiểm tra enum consistency

**Điều kiện:** có danh sách giá trị hợp lệ từ nghiệp vụ hoặc bảng danh mục.

**Cách thực hiện:** lấy `DISTINCT` các cột trạng thái/loại/giới tính/nhóm máu và đối chiếu với danh sách chuẩn.

**Convention hiện tại:** `data_quality/consistency/step4_enum_check.py` tự nhận
diện cột ứng viên theo tên và ghi rule `need_check` vào
`confirmed_business/consistency/enum_checks.yaml`. Data owner điền
`allowed_values` rồi chuyển rule thành `status: confirmed`. Query chỉ chạy rule
đã xác nhận và trả về `checked_count`, `invalid_count`,
`invalid_distinct_count`, `invalid_values` và `invalid_pct`.

### Bước 5 — Kiểm tra cross-table consistency

**Điều kiện:** xác định các thuộc tính được lưu lặp hoặc denormalize.

**Cách thực hiện:** so sánh giá trị giữa bảng nguồn và bảng giao dịch, hoặc tính lại số liệu tổng hợp từ bảng chi tiết.

**Convention hiện tại:** `data_quality/consistency/step5_cross_table_check.py`
dùng quan hệ từ Bước 1 và tên cột trùng nhau để tạo ứng viên
`duplicated_value` trong `confirmed_business/consistency/cross_table_checks.yaml`.
Data owner xác nhận cột nguồn, cột đích, khóa nối và `null_policy`, sau đó
chuyển rule thành `status: confirmed`. Query chỉ chạy rule đã xác nhận và trả
về `checked_count`, `violation_count` và `violation_pct`.

Orchestrator ghi kết quả vào workbook riêng `{DB_NAME}_consistency.xlsx`; mỗi
sheet tương ứng một step kiểm tra từ Bước 2 đến Bước 5. Bước 1 chỉ cung cấp
quan hệ đầu vào và không tạo sheet kết quả.

## 4. Uniqueness — Tính duy nhất

**Mục tiêu:** phát hiện trùng kỹ thuật và trùng cùng một thực thể nghiệp vụ.

### Bước 1 — Chọn phạm vi

**Điều kiện:** dùng Tier và danh sách bảng nghiệp vụ trọng yếu.

**Cách thực hiện:** ưu tiên bảng bệnh nhân, nhân viên, danh mục, hồ sơ, đơn thuốc, hóa đơn và các cột định danh như mã bệnh nhân, CCCD, BHYT, mã hồ sơ, số hóa đơn.

### Bước 2 — Kiểm tra trùng kỹ thuật

**Điều kiện:** cần xác định cột nghiệp vụ; loại ID kỹ thuật và timestamp khỏi khóa so sánh.

**Cách thực hiện:** đếm các nhóm có cùng toàn bộ giá trị nghiệp vụ; kết quả dương tính chỉ là danh sách nghi ngờ.

**Hiện trạng code:** `data_quality/uniqueness/step2_technical_duplicate.py`
gộp phạm vi của Bước 1 vào Layer 1 của Bước 2: chỉ quét bảng Tier 1 và loại
trừ log, audit, history, cấu hình, danh mục; Layer 2 tự động nhóm toàn bộ cột
nghiệp vụ sau khi bỏ ID kỹ thuật và timestamp. Kết quả được ghi bởi
`data_quality/load_uniqueness.py` vào workbook `{DB_NAME}_uniqueness.xlsx`.

### Bước 3 — Kiểm tra trùng nghiệp vụ

**Điều kiện:** nghiệp vụ cung cấp tổ hợp nhận diện thực thể.

**Cách thực hiện:** kiểm tra các tổ hợp như họ tên + ngày sinh + giới tính, CCCD, BHYT; chuẩn hóa encoding và dấu trước khi so sánh.

### Bước 4 — Kiểm tra UNIQUE constraint thực tế

**Điều kiện:** lấy danh sách UNIQUE constraint từ metadata.

**Cách thực hiện:** chạy lại phép nhóm/đếm trên dữ liệu hiện tại để phát hiện constraint khai báo nhưng đang bị vi phạm hoặc không phản ánh đúng dữ liệu.

**Hiện trạng code:** `data_quality/uniqueness/step4_unique_constraint.py` lấy
UNIQUE constraint trực tiếp từ PostgreSQL, hỗ trợ khóa ghép và kiểm tra lại dữ
liệu hiện tại. Kết quả được ghi vào sheet `Step 4 - Unique Constraint` trong
workbook `{DB_NAME}_uniqueness.xlsx`. Bước 3 và Bước 5 hiện chưa triển khai.

### Bước 5 — Đánh giá ảnh hưởng

**Điều kiện:** có danh sách duplicate từ Bước 2–4.

**Cách thực hiện:** phân tích thời điểm phát sinh, tỷ lệ trên tổng dữ liệu và liên kết với giao dịch.

## 5. Accuracy — Tính chính xác

**Mục tiêu:** xác định dữ liệu có phản ánh đúng thực tế hay không.

### Bước 1 — Xác định phạm vi có thể kiểm tra

**Điều kiện:** cần nguồn chuẩn bên ngoài hoặc bằng chứng nghiệp vụ.

**Cách thực hiện:** tách nội dung có thể đối chiếu tự động, như mã ICD-10, mã thuốc, mã hành chính, khỏi nội dung cần kiểm tra hồ sơ thực tế.

### Bước 2 — Đối chiếu danh mục chuẩn

**Cách thực hiện:** chuẩn bị danh mục chuẩn, đối chiếu mã, đếm mã không tồn tại/thay thế và số record bị ảnh hưởng.

### Bước 3 — Kiểm tra mẫu

**Cách thực hiện:** lấy mẫu ngẫu nhiên có phân tầng theo khoa và năm; ghi rõ cỡ mẫu, phương pháp và bằng chứng xác minh.

### Bước 4 — Ghi nhận giới hạn

**Đầu ra:** phần đã kiểm tra, phần không thể kiểm tra và lý do.

## 6. Timeliness — Tính kịp thời

**Mục tiêu:** xác định dữ liệu có được tạo/cập nhật đúng thời điểm nghiệp vụ hay không.

### Bước 1 — Xác định bảng còn hoạt động

**Điều kiện:** bảng có `created_at`, `updated_at` hoặc cột thời gian tương đương; loại trừ bảng danh mục tĩnh.

**Cách thực hiện:** lấy bản ghi mới nhất và phân nhóm đang hoạt động, nghi ngờ hoặc bị bỏ rơi.

### Bước 2 — Đo độ trễ nhập/cập nhật

**Điều kiện:** nghiệp vụ xác định cột thời điểm sự kiện và thời điểm ghi nhận.

**Cách thực hiện:** tính trung bình, trung vị, max và outlier của khoảng thời gian giữa hai mốc.

### Bước 3 — Kiểm tra độ phủ lịch sử

**Cách thực hiện:** ghi nhận record cũ nhất/mới nhất và phát hiện khoảng thời gian bị đứt ở Tier 1.

### Bước 4 — Kiểm tra tần suất bất thường

**Cách thực hiện:** nhóm record theo ngày/tháng để phát hiện tần suất quá thấp hoặc đột biến.

## 7. Validity — Tính hợp lệ

**Mục tiêu:** kiểm tra định dạng, miền giá trị và quy tắc kỹ thuật; không kết luận dữ liệu đúng thực tế.

### Bước 1 — Phân nhóm cột

**Điều kiện:** cần metadata column.

**Cách thực hiện:** nhóm theo kiểu dữ liệu và pattern tên cột: ngày tháng, số đo, mã định danh, liên lạc, danh mục.

### Bước 2 — Áp dụng rule theo nhóm

**Cách thực hiện:** kiểm tra ngày quá cũ/tương lai, số ngoài khoảng, mã sai độ dài/pattern, email/phone sai format và enum ngoài tập hợp.

### Bước 3 — Lọc kết quả

**Điều kiện:** có Tier và ngưỡng tỷ lệ vi phạm.

**Cách thực hiện:** ưu tiên Tier 1 và cột có tỷ lệ vi phạm vượt ngưỡng; Tier thấp hơn ghi tổng hợp.

## 8. Integrity — Tính toàn vẹn chuỗi

**Mục tiêu:** kiểm tra toàn bộ chuỗi nhiều mắt xích, khác với Consistency chỉ kiểm tra từng cặp FK–PK.

### Bước 1 — Xác định chuỗi và tính bắt buộc

**Điều kiện:** đã có sơ đồ quan hệ từ Consistency và nghiệp vụ xác nhận mắt xích bắt buộc/tùy chọn.

**Cách thực hiện:** gắn nhãn bắt buộc hoặc tùy chọn cho từng cạnh trong chain.

### Bước 2 — Kiểm tra mắt xích bị đứt

**Cách thực hiện:** tìm record tồn tại ở bảng trước nhưng không có record tương ứng ở mắt xích bắt buộc tiếp theo.

### Bước 3 — Đo tỷ lệ chuỗi hoàn chỉnh

**Đầu ra:** tổng record, record hoàn chỉnh, số record đứt chuỗi và tỷ lệ lỗi theo từng chain.

## 9. Temporal Consistency — Nhất quán theo thời gian

**Mục tiêu:** kiểm tra cùng một thực thể có bị mâu thuẫn giữa các thời điểm hay không.

### Bước 1 — Xác định thực thể và lịch sử

**Điều kiện:** cần khóa thực thể, cột hiệu lực thời gian hoặc bảng history/audit.

**Cách thực hiện:** xác định các phiên bản của cùng thực thể và thứ tự thời gian.

### Bước 2 — Kiểm tra khoảng thời gian

**Cách thực hiện:** phát hiện khoảng hiệu lực chồng lấn, đảo thứ tự thời gian, bản ghi quay về trạng thái cũ bất thường.

### Bước 3 — Đối chiếu trạng thái

**Điều kiện:** nghiệp vụ cung cấp state transition hợp lệ.

**Cách thực hiện:** kiểm tra chuyển trạng thái có đúng thứ tự và có sự kiện bắt buộc kèm theo hay không.

## 10. Lineage — Truy xuất nguồn gốc

**Mục tiêu:** biết dữ liệu đến từ đâu để khoanh vùng lỗi.

### Bước 1 — Liệt kê nguồn nhập liệu

**Cách thực hiện:** phân loại nhập tay, migration, thiết bị y tế, BHXH, API hoặc nguồn khác.

### Bước 2 — Kiểm tra dấu vết nguồn

**Cách thực hiện:** tìm các cột `created_by`, `source`, `data_source`, bảng audit/log và metadata ETL.

### Bước 3 — So sánh chất lượng theo nguồn

**Điều kiện:** có thể gắn record với nguồn.

**Cách thực hiện:** so sánh NULL, placeholder, validity và orphan rate theo từng nguồn.

## 11. Quy tắc bàn giao kết quả

Mỗi dimension phải bàn giao tối thiểu:

1. Phạm vi bảng/cột đã kiểm tra.
2. Điều kiện và rule đã sử dụng.
3. Kết quả định lượng: số lượng, tỷ lệ, số record ảnh hưởng.
4. Các giả định và xác nhận từ nghiệp vụ.
5. Các phần chưa kiểm tra được và lý do.
6. Query/script hoặc entry point dùng để tái chạy.

Không kết luận lỗi chỉ từ ngưỡng kỹ thuật nếu chưa kiểm tra điều kiện nghiệp vụ. Đặc biệt, NULL cao có thể là `Meaningful NULL`, FK NULL có thể là quan hệ tùy chọn, và giá trị ngoài khoảng có thể là trường hợp hợp lệ cần xác nhận.
