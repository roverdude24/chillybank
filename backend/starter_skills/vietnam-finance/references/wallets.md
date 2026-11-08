# Vietnamese e-wallets

Every wallet is a `cash` account (no wallet type exists). `institution` = the wallet name.

| Wallet | Domain |
|---|---|
| MoMo | momo.vn |
| ZaloPay | zalopay.vn |
| ShopeePay | shopeepay.vn |
| VNPAY (VNPAY QR) | vnpay.vn |
| Viettel Money | viettelmoney.vn |
| VNPT Money | vnptmoney.vn |

Apple Pay, Google Pay and similar are payment methods over a card or bank account, not
accounts: log the spend on the underlying account.

VNPAY QR is a payment rail used by many banking apps. A purchase by VNPAY QR from a bank app is
a spend on that bank account, not on a VNPAY wallet.

## Rules

1. Top-up from bank to wallet (`nạp tiền ví`, `nap tien vi`, `NAP MOMO`) = transfer, bank →
   wallet, no category. The same for withdrawal from wallet to bank (`rút tiền`).
2. The spend is logged once, from the wallet, when it pays a merchant, a bill or a person.
3. Wallet-to-wallet or wallet-to-person (`chuyển tiền`) to someone else is an expense or a
   gift/family category by what the user says; to the user's own account it is a transfer.
4. Cashback, vouchers and promotions credited to the wallet are income-like rows. Ask once how
   the user wants them (a reduction of the category, or "Cashback"), then keep to it.
5. A balance shown in the app is the user's source of truth. When it differs from the ledger,
   use the balance-mismatch-tracer skill rather than adjusting blindly.
6. Wallet mail and SMS alerts carry the merchant and amount; keep the raw text in the
   description.
7. Utility bills (điện, nước, internet), phone top-ups and ticket purchases paid through a wallet
   are expenses from the wallet; categorize with `merchants.md`.
8. Buy-now-pay-later in a wallet (MoMo Ví Trả Sau, ShopeePay Later, etc.) is a `credit` account;
   settling it from the wallet or bank is a transfer.
