# Merchants and bills → category

Suggestions only: use the closest real label in the user's category list, or leave it unset.
Statements show ASCII uppercase text; the first column is a stable substring to match.

| Statement text contains | Payee | Suggested category |
|---|---|---|
| GRAB | Grab (ride) | Transport |
| GRABFOOD / GRAB*FOOD | GrabFood | Dining out / Food delivery |
| GRABMART | GrabMart | Groceries |
| BE GROUP / BE TECH / BEFOOD | Be | Transport (BeFood: Dining out) |
| XANH SM / GSM | Xanh SM | Transport |
| SHOPEE | Shopee | Shopping |
| LAZADA | Lazada | Shopping |
| TIKI | Tiki | Shopping |
| TIKTOK SHOP / TIKTOK | TikTok Shop | Shopping |
| CIRCLE K / CIRCLEK | Circle K | Groceries / Convenience |
| GS25 | GS25 | Groceries / Convenience |
| WINMART / WIN MART | WinMart | Groceries |
| BACH HOA XANH / BHX | Bách Hóa Xanh | Groceries |
| COOPMART / CO.OPMART / SAIGON CO.OP | Co.opmart | Groceries |
| HIGHLANDS / HIGHLANDS COFFEE | Highlands Coffee | Coffee / Dining out |
| PHUC LONG | Phúc Long | Coffee / Dining out |
| STARBUCKS | Starbucks | Coffee / Dining out |
| EVN / DIEN LUC / TIEN DIEN | EVN (điện) | Utilities |
| SAWACO / CAP NUOC / TIEN NUOC | SAWACO (nước) | Utilities |
| FPT TELECOM / FPT | FPT (internet) | Utilities / Internet |
| VNPT | VNPT (internet, điện thoại) | Utilities / Internet |
| VIETTEL | Viettel (internet, điện thoại) | Utilities / Phone |
| MOBIFONE / VINAPHONE | Mobifone / Vinaphone | Utilities / Phone |
| HOC PHI / TRUONG | Học phí (tuition) | Education |
| BHXH / BAO HIEM XA HOI | Bảo hiểm xã hội | Insurance / Taxes & contributions |
| BHYT / BAO HIEM Y TE | Bảo hiểm y tế | Insurance / Health |
| LONG CHAU | Long Châu | Health / Pharmacy |
| PHARMACITY | Pharmacity | Health / Pharmacy |

Other common ones: Netflix, Spotify, YouTube Premium, Google, Apple → Subscriptions; gas
(PETROLIMEX, PVOIL) → Transport / Fuel; CGV, Lotte Cinema → Entertainment; Vietnam Airlines,
Vietjet, Bamboo, Traveloka, Agoda → Travel; Bệnh viện / Phòng khám → Health.

## Notes

- Each row above still needs the user's own category label. Read the category list first and
  map to it; do not create categories to match this table.
- Parent labels: a Grab ride and GrabFood share the same company but different categories.
  Use the substring (GRABFOOD) in a rule so they separate.
- The utilities of a rented or owned home may be shared with a landlord or partner: the user
  decides whether the share is an expense or a reimbursement.
- Receipts from Shopee, Lazada or TikTok Shop usually carry the item, which fixes the
  category better than the platform; use it when the user supplies it.

## Transfer memos

These almost always mean the row is a transfer, a salary or a person-to-person payment, not a
merchant purchase.

| Memo contains | Meaning |
|---|---|
| CK, CHUYEN KHOAN, CHUYEN TIEN | A bank transfer. Look at the name and note after it. |
| NAPAS, IBFT, LIÊN NGÂN HÀNG | Interbank rail. Direction comes from ghi nợ / ghi có. |
| MBVCB, VCB, INTERNET BANKING | The sending channel of a bank. Not a merchant. |
| NAP TIEN VI, NAP MOMO, NAP ZALOPAY | Wallet top-up → transfer to the wallet account. |
| RUT TIEN | Cash withdrawal (ATM) → transfer to the cash-in-hand account. |
| TRA THE, THANH TOAN THE TIN DUNG | Credit card payment → transfer to the credit account. |
| LUONG, LUONG THANG | Salary income. |
| LAI TIEN GUI, LAI | Deposit interest income. |
| PHI DUY TRI, PHI SMS, PHI QUAN LY | Bank fees → Bank fees. |
| TRA NO, GIAI NGAN | Loan repayment or drawdown (see `housing.md`). |

When the memo is only a name plus `CK`, it is a person-to-person payment: ask once what it was
for, then add a payee rule for that person.
