# Housing in Vietnam

## The property itself

- Create it with `create_asset`, category `property`, name by address or project (the district
  and project: "Căn hộ 2PN, Thủ Đức"), `value` = the user's current estimate in VND, `cost` and
  `cost_date` = purchase price and date, `share_pct` = the user's share when co-owned
  (`share_note`: "đứng tên chung với vợ/chồng").
- Document type goes into the description or facts, as the user states it:
  - **Sổ đỏ** (Giấy chứng nhận quyền sử dụng đất) — land use rights certificate.
  - **Sổ hồng** (Giấy chứng nhận quyền sở hữu nhà ở) — house/apartment ownership.
  - Apartments in a project not yet handed over may have only a purchase contract (hợp đồng
    mua bán / HĐMB): say so; the sổ hồng arrives after handover.
- Value changes: `record_asset_value` with a date and a note naming the source. Market price
  per m² in HCMC (Thủ Đức, Quận 2/9 cũ, Bình Thạnh, ...) is entered by the user — from a
  listing, a broker or an appraisal. Never invent it. Ask: price per m² × area, or a total.
- State the age of the last valuation when reporting net worth.
- A vehicle (ô tô, xe máy) is category `vehicle`; the registration fee and the plate fee
  (phí trước bạ, biển số) are separate expenses.

## Mortgage (vay mua nhà)

1. `create_account`, type `loan`, institution = the bank, name "Vay mua nhà <bank>". Opening
   balance = principal outstanding, as a liability.
2. Link it to the property through the asset's loan account field (`update_asset`), so net equity
   reads property value minus the loan.
3. `create_commitment` kind `loan_repayment`: principal, APR (%/năm), term in months, the
   monthly installment, linked to the loan account.
4. Vietnamese mortgages usually have a promotional fixed rate (lãi suất ưu đãi) for the first
   6 to 36 months and a floating rate afterwards (biên độ cộng lãi suất tiết kiệm). Ask for the
   current rate and the date it resets; do not assume the later rate.
5. Each monthly payment from the bank account is a transfer from the cash account to the loan
   (principal) plus the interest as an expense row. Take the split from the bank's schedule
   (lịch trả nợ), not from a calculation, because banks compute interest on actual days.
6. Early repayment (tất toán trước hạn) usually carries a fee (phí trả nợ trước hạn); log it as
   a separate expense.

## Purchase installments (mua căn hộ trả góp theo tiến độ)

Developer schedule payments (đợt 1, đợt 2, ...) are a `purchase` commitment with one
installment per schedule date and amount. Each payment moves cash to the asset's cost; it is not
an ordinary expense. The bank loan, when disbursed, pays the developer directly: record that as a
loan drawdown on the loan account, not as cash arriving in the user's account.

## Rent

- **Rent paid** (tiền thuê nhà): monthly expense, payee the landlord; set a payee rule or a
  commitment of kind `auto_pay` so it appears every month. Deposit (tiền cọc) is a
  refundable `receivable` account, not an expense.
- **Rent received** (thu nhập cho thuê): income on the receiving account, by property. A
  deposit held from a tenant is not income; it is owed back.
- Landlord tax and the like: do not state rates here; ask the user for what they pay.

## Running costs

Management fee (phí quản lý chung cư), parking (phí giữ xe), utilities (`merchants.md`),
maintenance fund (quỹ bảo trì, 2% of the apartment price at handover, paid once): expenses with
category from `merchants.md`; the maintenance fund can be added to the property's cost.
Annual land-use tax (thuế sử dụng đất phi nông nghiệp) is an expense each year.
