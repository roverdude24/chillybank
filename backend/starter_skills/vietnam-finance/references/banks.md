# Vietnamese banks

Use the short name as the account `institution` so every account of one bank groups together.
The logo is fetched by domain. Create the institution/account only when the user names it.

| Short name | Full name | Domain |
|---|---|---|
| Vietcombank | Ngân hàng TMCP Ngoại thương Việt Nam | vietcombank.com.vn |
| VietinBank | Ngân hàng TMCP Công thương Việt Nam | vietinbank.vn |
| BIDV | Ngân hàng TMCP Đầu tư và Phát triển Việt Nam | bidv.com.vn |
| Agribank | Ngân hàng Nông nghiệp và Phát triển Nông thôn | agribank.com.vn |
| Techcombank | Ngân hàng TMCP Kỹ thương Việt Nam | techcombank.com |
| MB | Ngân hàng TMCP Quân đội (MB Bank) | mbbank.com.vn |
| ACB | Ngân hàng TMCP Á Châu | acb.com.vn |
| VPBank | Ngân hàng TMCP Việt Nam Thịnh Vượng | vpbank.com.vn |
| TPBank | Ngân hàng TMCP Tiên Phong | tpb.vn |
| Sacombank | Ngân hàng TMCP Sài Gòn Thương Tín | sacombank.com.vn |
| HDBank | Ngân hàng TMCP Phát triển TP.HCM | hdbank.com.vn |
| VIB | Ngân hàng TMCP Quốc tế Việt Nam | vib.com.vn |
| SHB | Ngân hàng TMCP Sài Gòn - Hà Nội | shb.com.vn |
| OCB | Ngân hàng TMCP Phương Đông | ocb.com.vn |
| MSB | Ngân hàng TMCP Hàng Hải Việt Nam | msb.com.vn |
| SeABank | Ngân hàng TMCP Đông Nam Á | seabank.com.vn |
| LPBank | Ngân hàng TMCP Lộc Phát Việt Nam (trước đây LienVietPostBank) | lpbank.com.vn |
| Eximbank | Ngân hàng TMCP Xuất Nhập khẩu Việt Nam | eximbank.com.vn |
| Nam A Bank | Ngân hàng TMCP Nam Á | namabank.com.vn |
| Timo | Ngân hàng số Timo | timo.vn |
| Cake | Cake by VPBank (ngân hàng số) | cake.vn |

If a domain here fails to resolve a logo, leave the logo as is; do not substitute a guess.

## Statement formats

Not verified per bank here. Do not claim a bank's export layout from memory. What the importer
handles for any bank: Vietnamese column headers (ngày, số tiền, ghi nợ, ghi có, số dư, nội
dung, diễn giải) and dd/mm/yyyy dates. Two shapes appear in practice:

- One signed `số tiền` column: negative = money out.
- Split `ghi nợ` (debit, money out) and `ghi có` (credit, money in) columns: the filled one
  decides the direction.

Check the first imported rows against the bank's app before importing the rest, and use the
balance check (`số dư`) when the file has a running balance.

## Account notes

- Current/payment account (tài khoản thanh toán) → `cash`.
- Savings book / online term deposit (sổ tiết kiệm) → `fixed_deposit`.
- Credit card (thẻ tín dụng) → `credit`.
- Personal or home loan at the bank → `loan` (see `housing.md`).
- Bank "tiết kiệm linh hoạt / không kỳ hạn" that can be withdrawn any day → `cash`, a separate
  account so it is not mixed with the spending account.
