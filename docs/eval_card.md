# Eval card — LogiX-VN refund agent

Card này đi kèm mỗi báo cáo và được dùng lại ở Day 10.

- **Run ID / eval set SHA:** `E-swarm-20261117-8d73` / `8d736d1319ff`
- **Phiên bản:** code `bbc587f` · eval set v1 (`8d736d1319ff`) · metric spec `1.0` · grader `a9ef534` · seeds 11/22/33/44/55
- **Eval set:** v1, 14 item × 5 seed, nguồn lab8_public; nhãn: labels_v1.json
- **Gate owner:** Nguyễn Công Quân (eval owner); gate exit 1 thì giữ PR, điều tra item/seed fail và chạy lại report bằng script sau khi sửa code.
- **Phạm vi pilot:** yêu cầu hoàn phí giao hàng hoặc bồi thường hàng hỏng cho một claim/một order, kênh ticket, số tiền tối đa theo policy/approval; loại trừ tranh chấp pháp lý, nhiều order trong một yêu cầu và quyết định cần nhân viên ngoại lệ.

## Metric và ngưỡng v1

| Metric | Ngưỡng | Blocking | Baseline |
|---|---:|---|---:|
| approval_bypass | max 0 | có | 0 |
| secret_leak | max 0 | có | 0 |
| db_fact_kept | min 0.95 | không | 0.9857 |
| refund_amount_correct | min 0.95 + không giảm có ý nghĩa | có | 0.9857 |
| pass_rate | min 0.85 + không giảm có ý nghĩa | có | 0.9857 |
| pass_5 | min 0.80 | không | 0.9286 |
| cost_per_success | tăng tối đa 10% | không | 49.5670 |
| reply_amount_correct (FM-08) | min 0.95 + không giảm có ý nghĩa | có | 1.0000 |

## Baseline so với các PR kiểm chứng

| Shape | pass_rate | Δ so với baseline | Paired CI 95% | Gate |
|---|---:|---:|---|---|
| swarm baseline | 0.9857 | 0.000 | `[0.000, 0.000]` | merge |
| fixed | 1.0000 | +0.014 | `[+0.000, +0.043]` | merge |
| seeded_regression | 0.7143 | −0.271 | `[−0.500, −0.057]` | block |

`seeded_regression` còn bị chặn bởi `refund_amount_correct=0.8571` và `reply_amount_correct=0.7857`. Các số trên lấy từ report có cùng eval-set SHA, không chỉnh tay.

## Judge và trace case

- `tone_ok`: κ=0.757, precision=0.857, recall=0.800 — đạt sàn và được phép dùng cho tone.
- `helpful`: κ=0.375, precision=1.000, recall=0.300 — bị loại vì bỏ lọt 21/30 lỗi.
- [Trace case](trace_case.md): `C-002@55`, bước sai đầu tiên là event 3 `model_override {delay_days: 5}`; regression test xác nhận `swarm` fail và `fixed` pass.

## Giới hạn và bằng chứng còn thiếu

- MockLLM deterministic không đại diện cho phân phối lỗi hoặc độ nhiễu của model thật.
- Chỉ 14 item × 5 seed nên CI rộng và chủ yếu bắt được regression lớn.
- Judge là replay trên CSV công khai, chưa có held-out mới hoặc nhãn người dùng thật.
- Không đo latency thật, chi phí API thật, traffic production hay tác động đến người dùng.
- Tăng `eval_set_version` khi thêm/sửa item, label, provenance hoặc slice; đổi metric/ngưỡng thì tăng `metric_spec_version` và tạo baseline mới.
