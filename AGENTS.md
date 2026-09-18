# AGENTS.md

## Hướng dẫn làm việc

- Tuân thủ nguyên tắc YAGNI: chỉ triển khai đúng phạm vi yêu cầu hiện tại.
- Trước khi chỉnh sửa code, đọc tài liệu chỉ mục tại [docs/CODEBASE_INDEX.md](docs/CODEBASE_INDEX.md).
- Chỉ đọc thêm các file liên quan trực tiếp đến yêu cầu; không mở rộng phạm vi không cần thiết.
- Giữ thay đổi nhỏ, rõ ràng và không sửa code không liên quan.
- Không đưa thông tin nhạy cảm trong `.env` vào log, tài liệu hoặc output.

## Python runtime

Mọi lệnh Python, kiểm tra cú pháp và test phải dùng conda environment:

```bash
conda run -n learn_data_engineer python <command>
```

Không mặc định dùng Python của `base` hoặc system Python.

## Codebase index

- [docs/CODEBASE_INDEX.md](docs/CODEBASE_INDEX.md): giải thích ý nghĩa các file và cấu trúc codebase.

Đây là tài liệu tham chiếu chính để hiểu repository trước khi thực hiện code. Khi cấu trúc hoặc vai trò file thay đổi, cập nhật `docs/CODEBASE_INDEX.md` trong cùng thay đổi nếu cần.

## Kiểm tra sau khi thay đổi

Tối thiểu chạy compile bằng đúng environment:

```bash
conda run -n learn_data_engineer python -m py_compile <changed-files>
```

Nếu thay đổi có thể kiểm thử mà không cần database, phải chạy smoke test tương ứng. Nếu cần database hoặc dependency ngoài, ghi rõ giới hạn kiểm tra trong kết quả bàn giao.
