---
name: vietnam-finance
description: "Use this skill whenever the user's money is in Vietnam — a Vietnamese bank or e-wallet (Vietcombank, Techcombank, MB, MoMo, ZaloPay...), a VN stock or fund, SJC gold, a house or apartment in Vietnam, a mortgage, or any amount in VND/đồng/nghìn/triệu/tỷ. Use it to set accounts up, log or import transactions, categorize Vietnamese merchants and bills, and answer in Vietnamese with VND formatting, even when the user never says 'Vietnam'. Not for generic budgeting or investment reviews (budget-review, investment-checkup) unless the money is Vietnamese."
---

Model Vietnamese money with the tools and account types that already exist — no new feature is
needed. The base currency is the đồng (₫). Reply in the user's language; for a Vietnamese user that is
Vietnamese with diacritics.

## Gotchas

- The đồng has no minor unit: whole đồng only, never decimals. Amounts like `1,250,000` in a
  statement are one million two hundred fifty thousand.
- Typed shorthand: `50k` / `50 nghìn` = 50.000; `2tr` / `2 triệu` = 2.000.000; `1 tỷ` =
  1.000.000.000; `2tr5` = 2.500.000. Confirm anything ambiguous before booking.
- There is no e-wallet account type. MoMo, ZaloPay, ShopeePay, VNPAY etc. are `cash` accounts.
- Topping up a wallet from a bank, paying a credit card, or moving money between own accounts is
  a transfer, never an expense. Count the spend once, when the wallet pays the merchant.
- Never use `asset_class` commodity with `unit` luong for a price per lượng in đồng: it applies a
  gram/ounce conversion on top of a price that is already per lượng. SJC gold is a manual
  instrument (see Gold).
- Never guess a ticker's exchange. HOSE prices come from Yahoo as `XXX.VN`; HNX and UPCoM do not.
- Never auto-create an institution or account the user did not name. Ask.
- Do the money math with the calculate tool, never in your head.

## Formatting đồng amounts

- Dots as thousands separators, no decimals, symbol after the number with a space: `1.250.000 ₫`.
- Round figures in prose: `15 triệu`, `1,2 tỷ` (comma as the decimal mark inside words).
  Give the exact `1.250.000 ₫` form in tables and totals.
- Dates `dd/mm/yyyy`. Name the months in Vietnamese (tháng 11).
- Say "chuyển khoản" for transfer, "chi tiêu" expense, "thu nhập" income, "số dư" balance,
  "tiết kiệm" savings, "khoản vay" loan, "tài sản" asset.

## Modelling each thing

1. **Bank account** → `create_account`, type `cash`, `institution` = short bank name from
   `references/banks.md`, currency the đồng (code in the currency field). The favicon logo is looked up by the bank's domain,
   which the reference file lists.
2. **E-wallet** → `cash` account under the wallet's name (`references/wallets.md`). A top-up is
   a transfer from the bank account.
3. **Savings with a term (tiết kiệm có kỳ hạn)** → `fixed_deposit` account. Ask principal,
   rate, start date, term; log interest when the bank pays it, as income.
4. **Credit card** → `credit` account; the monthly payment from the bank is a transfer.
5. **Stocks, ETFs, funds, SJC gold, crypto** → read `references/investments.md` first. An
   `investment` account per brokerage (SSI, VPS, Vndirect, TCBS, ...).
6. **House, apartment, land, car, motorbike** → owned asset via `create_asset`
   (category `property` or `vehicle`), later valuations via `record_asset_value`. Mortgage is a
   `loan` account plus a commitment. Read `references/housing.md`.
7. **Statements and bills** → import with the statement tools; Vietnamese headers (ngày, số
   tiền, ghi nợ, ghi có, số dư, nội dung, diễn giải) and dd/mm/yyyy dates are detected.
   Keep the bank's `nội dung` text verbatim as the description.
8. **Salary** → income on the receiving bank account. Employer BHXH/BHYT deductions are already
   out of a net salary; log the net amount received unless the user wants the gross.

## Categorizing

1. Read the user's category list and payee book first — both are user-edited. Use only real
   labels; leave the category unset when unsure.
2. Match the payee with `references/merchants.md` (merchant → category suggestion). Create the
   payee, give it a default category, and add a contains-rule on the stable merchant text in
   the bank string (`add_payee_rules`), never on a reference number.
3. Transfer memos (`CK`, `chuyen khoan`, `NAPAS`, `nap tien vi`, `MBVCB`, `IBFT`, ...): see the
   end of `references/merchants.md`. A memo naming a wallet or own account is a transfer.
4. Bank statements often strip diacritics and uppercase everything
   (`GRAB*A-12345 HO CHI MINH`). Match on the ASCII form; keep the display name properly
   accented (`Bách Hóa Xanh`).
5. Rent, tuition and family remittances (gửi tiền cho ba mẹ) are normal expenses or transfers
   by what the user says — ask once, then add a payee rule so it is automatic.

## Replying

- Lead with the answer in a line, then the figures.
- Money shape: `Số dư: 12.450.000 ₫`. Percent with a comma if the user writes Vietnamese
  (`6,5%/năm`); interest rates for deposits and loans are per year (%/năm).
- Do not convert to a foreign currency unless asked; when asked, state the rate and its date.
- Prices of gold, property per m² and manual instruments are what the user (or the refresh job)
  last entered. Say how old the figure is rather than presenting it as live.
- A figure you cannot source (a bank's current rate, a market price) is asked for, not guessed.

## References — read on demand

- `references/banks.md` — banks, domains, statement formats.
- `references/wallets.md` — MoMo, ZaloPay, ShopeePay, VNPAY, Viettel Money, VNPT Money.
- `references/investments.md` — stocks, ETFs, funds, bonds, gold, crypto, VN tax.
- `references/housing.md` — property, sổ đỏ/sổ hồng, mortgage, rent.
- `references/merchants.md` — merchant and bill → category, transfer memos.
