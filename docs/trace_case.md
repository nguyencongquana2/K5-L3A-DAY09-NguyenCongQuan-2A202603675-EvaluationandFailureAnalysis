# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case đã điều tra

| Trường | Nội dung |
|---|---|
| Trial | `swarm C-002@55`; baseline run `E-swarm-20261117-8d73` |
| Triệu chứng | `TrialPass`, `RefundAmountCorrect` và `DbFactKept` fail: kết quả báo resolved và ledger ghi 20.000đ trong khi label yêu cầu denied/0đ. |
| Bước sai đầu tiên | Event 3, `model_override {"delay_days": 5}`. |
| Vì sao là bước *đầu tiên* | Event 1 tạo handoff đúng scope; event 2 đọc order O-202 từ DB với `delay_days=2`. Sai lệch chỉ xuất hiện khi event 3 thay giá trị DB bằng số trong ticket. |
| Failure mode | FM-03 `ticket_trust`. |
| Đối chứng | `fixed C-002@55` không có `model_override`; event 3 là quote 0/no_entitlement, ledger 0 và status denied. |
| Regression test | `test_ticket_override_is_first_wrong_step_and_fixed_removes_it`: xác nhận trace lỗi có override sau investigator, grader fail; `fixed` không có override và grader pass. |
| Chưa chắc chắn | Case xác nhận lỗi trên dữ kiện số ngày; chưa chứng minh agent chống được mọi dạng dữ kiện ticket giả hoặc prompt injection khác. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
