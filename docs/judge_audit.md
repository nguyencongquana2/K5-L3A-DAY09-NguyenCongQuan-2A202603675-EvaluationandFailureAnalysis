# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Có | dùng cho gate tone, tiếp tục audit định kỳ |
| helpful | 100 | 0.375 | 1.000 | 0.300 | Không (κ và recall dưới sàn) | loại khỏi gate |

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú |
|---|---|---|---|---|---|
| 1 | H-008 | PASS | FAIL | Người | “em/nha” quá suồng sã; rubric judge phải nêu rõ đại từ và tiểu từ bị cấm trong hỗ trợ khiếu nại. |
| 2 | H-029 | PASS | FAIL | Người | “không cần nhắn thêm” là mệnh lệnh; rubric phải xét toàn câu, không cho các từ mở đầu “Dạ/vui lòng” tự động cứu verdict. |
| 3 | H-042 | FAIL | PASS | Người | Câu ngắn nhưng đủ ý; bỏ yêu cầu ngầm rằng mọi reply phải xin lỗi. |
| 4 | H-051 | FAIL | PASS | Người | “refund” nằm trong từ vựng CSKH được phép; thêm danh sách thuật ngữ Việt–Anh chấp nhận được. |
| 5 | H-063 | PASS | FAIL | Người | Hứa “sẽ hoàn thêm” không có bằng chứng hệ thống; rubric phải đánh fail mọi cam kết ngoài ledger. |
| 6 | H-070 | PASS | FAIL | Người | Emoji cười không phù hợp ngữ cảnh khiếu nại; thêm ví dụ biên về emoji theo ngữ cảnh. |
| 7 | H-081 | FAIL | PASS | Judge | Nhãn người đọc nhầm ticket; giữ verdict judge và đưa case vào vòng adjudication để sửa nhãn ở phiên bản dataset kế tiếp. |
| 8 | HL-015 | PASS | FAIL | Người | Reply nói 55.000đ trong khi ledger 40.000đ; helpful rubric phải yêu cầu đối chiếu số tiền với structured context. |
| 9 | HL-047 | PASS | FAIL | Người | “Ok bạn… nhé” vừa suồng sã vừa không nói kết quả cụ thể; thêm tiêu chí action/result tối thiểu và xưng hô. |

## Kết luận

- `tone_ok` đạt cả ba sàn với ma trận `tp=24, fp=4, fn=6, tn=66`; có thể dùng nhưng cần adjudicate H-081 và theo dõi drift sau khi sửa rubric.
- `helpful` bị loại: ma trận `tp=9, fp=0, fn=21, tn=70` cho thấy judge bỏ lọt 70% lỗi. Precision 1.0 không bù được recall 0.3 và κ 0.375.
- Tiền, approval, principal scope và sự khớp reply–ledger luôn dùng code grader. Judge không được thay thế nguồn sự thật có cấu trúc.
- Judge mới chỉ được kiểm trên replay CSV công khai; trước khi dùng production cần một tập nhãn người held-out mới, double-label và adjudication bất đồng.
