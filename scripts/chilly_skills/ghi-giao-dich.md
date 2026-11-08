Dùng khi người dùng muốn ghi một khoản chi, thu hoặc chuyển tiền ("ăn trưa 85k MoMo", "Omachi trả 25tr vào MB", "nạp 2tr vào ví").

Sổ chính là Notion. Ghi vào Notion, ChillyBank tự đồng bộ trong 10 phút. Không tạo giao dịch trực tiếp trong ChillyBank cho tiền của gia đình.

1. Đọc số tiền: 85k = 85.000; 2tr = 2.000.000; 2tr5 = 2.500.000; 1 tỷ = 1.000.000.000. Hỏi lại khi không rõ.
2. Tìm trang liên quan bằng công cụ Notion (API-query-data-source):
   - Tài khoản: data source b431cd69-988c-4a78-81f9-214937e2ed82 (cột Tài khoản).
   - Ngăn tiền: f7b2be42-2ae0-4fa4-8eee-f56415410e17 (cột Ngăn tiền) — chỉ cho khoản Chi.
   - Tháng: fb106c98-5875-49a8-b3f9-008ae7e6c895 (cột Tháng dạng MM/YYYY).
   - Job freelance (chỉ khi là tiền dự án): bảng "2026 + future clients" nếu đọc được.
3. Tạo một dòng bằng API-post-page, parent data source b540b4e2-f94e-490f-aea8-760ed047d12d (Giao dịch):
   - Nội dung (title): mô tả ngắn, tiếng Việt.
   - Ngày: YYYY-MM-DD.
   - Số tiền: luôn số dương.
   - Loại: Chi | Thu | Chuyển nội bộ.
   - Tài khoản: relation 1 trang.
   - Ngăn tiền: relation 1 trang (khoản Chi).
   - Nguồn thu (khoản Thu): Lương Ánh | Project Vinh | Zenme 1% | Lãi tiền gửi | Khác.
   - Nguồn tiền: Quỹ 1 tỷ | Thu nhập | Tiền bán nhà (của mẹ) | Tiền riêng (hỏi nếu không rõ; tiền sinh hoạt thường là Quỹ 1 tỷ).
   - Tháng: relation tháng của Ngày.
   - Dự án freelance: relation tới job khi là Project Vinh.
4. Trước khi gửi, nhắc lại một dòng tóm tắt (số tiền, tài khoản, ngăn) để người dùng duyệt thẻ xác nhận.
5. Nạp ví, rút tiết kiệm, chuyển giữa tài khoản của mình: Loại = Chuyển nội bộ, Tài khoản = nơi tiền đi, Đến tài khoản = nơi tiền đến (ChillyBank ghi thành chuyển khoản, không tính là chi). Trả thẻ tín dụng vẫn ghi Chi vào ngăn Trả nợ tín dụng như trong sổ.
6. Sau khi tạo: báo đã ghi, ChillyBank và màn hình TV cập nhật trong khoảng 10 phút. Nếu số dư trong Notion cũng cần đổi, nhắc người dùng cập nhật cột Số dư.
