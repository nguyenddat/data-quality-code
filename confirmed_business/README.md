# Confirmed business rules

Thư mục này lưu các quy tắc và phân loại đã được nghiệp vụ/data owner xác nhận
dưới dạng YAML. Các file chỉ chứa quy tắc, không chứa kết quả kiểm tra hoặc dữ
liệu nhạy cảm.

Quy ước chung:

- `status` là `draft` cho nội dung đang chờ xác nhận và `confirmed` khi đã được
  data owner duyệt.
- Mỗi rule nên có `id`, `status`, `owner` và `note`.
- Không tự suy luận hoặc chuyển một cảnh báo kỹ thuật thành rule nghiệp vụ.

Các dimension có thể đọc file bằng `utils.confirmed_business.load_rules()`. Các
constraint dùng chung như `tier_constraints.yaml` đặt trực tiếp tại thư mục
này, không đặt trong thư mục riêng của dimension.

Ví dụ thêm phân loại bảng:

```yaml
rules:
  - id: public-benh_nhan-tier
    status: confirmed
    owner: data-owner
    schema: public
    table: benh_nhan
    tier: 1
```

Chỉ các rule có `status: confirmed` mới được sử dụng. Với Tier bảng, dùng
`utils.confirmed_business.load_table_tiers()`.
