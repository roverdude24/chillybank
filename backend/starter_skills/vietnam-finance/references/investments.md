# Vietnamese investments

Brokerage accounts (SSI, VPS, VNDirect, TCBS, HSC, MBS, ...) are `investment` accounts, one per
brokerage. The cash the broker holds is that account's settlement cash. Moving money from the
bank to the broker is a transfer.

## Stocks

- **HOSE** (and ETFs listed there): Yahoo prices them as `<TICKER>.VN`, e.g. `VNM.VN`,
  `FPT.VN`. Resolve with `resolve_instruments` query `VNM.VN`; the quote currency is VND.
- **HNX and UPCoM**: not on Yahoo. Create a manual instrument with
  `resolve_instruments`: `price_source` `manual`, `price_source_id` `vndirect:<TICKER>`
  (e.g. `vndirect:SHS`), `quote_currency` `VND`, `asset_class` `equity`. The price job
  refreshes these from VNDirect about every 15 minutes during the day; no manual price entry
  is needed while `price_source_id` is set. Instruments without that `price_source_id` are
  never refreshed — their price is whatever the user entered.
- Ask which exchange the ticker trades on when it is not obvious; the same letters can be a
  different listing.
- Lot size on HOSE is 100 shares for orders; odd lots exist. Quantities are whole shares.
- Settlement is T+2: cash and shares settle two business days after the trade. Use the trade
  date as the transaction date.

## ETFs and funds

- Listed ETFs (E1VFVN30, FUEVFVND, FUESSVFL, FUEMAV30, ... ) trade like stocks: HOSE ones are
  `<TICKER>.VN`. Set `asset_subclass` `etf`; `asset_class` is the underlying exposure (equity
  for an equity-index ETF).
- Open-end funds (quỹ mở: DCDS, VESAF, SSISCA, VCBF-BCF, ... ) have no feed. Create manual
  instruments, `quote_currency` `VND`, with the NAV per unit (giá trị tài sản ròng trên một
  chứng chỉ quỹ) entered by the user, e.g. from the fund manager's statement or website. State the
  NAV date when reporting. Buys are units = amount / NAV at the order's NAV date.
- Fund distribution fees and purchase/redemption fees are separate fee rows, not part of the
  NAV.

## Gold

- **SJC gold bar** (vàng miếng SJC): one manual instrument, symbol `SJC`,
  `price_source` `manual`, `price_source_id` `pnj:SJC`, `quote_currency` `VND`; price is per
  lượng, `unit` left empty, `asset_class` any except `commodity`. The price job refreshes it
  from PNJ about every 15 minutes during the day. Quantity = number of lượng (1 chỉ = 0,1
  lượng; 1 phân = 0,01 lượng).
- **Nhẫn trơn PNJ 999.9**: `pnj:N24K`, same setup, its own symbol and instrument.
- Never `commodity` + `unit` `luong` for a VND price (see SKILL.md Gotchas).
- Gold jewelry (trang sức) and one-off pieces can instead be an owned asset in category
  `metal`; the user enters valuations.
- The buy/sell price differ (chênh lệch mua - bán); the feed gives one price, so the value
  shown is not the realizable value. Mention that when reporting gold.

## Bonds, certificates, deposits

- Corporate or government bonds (trái phiếu): no feed. Manual instrument `asset_class` `bond`,
  or an `investment`-account holding at face value; the user enters price. Coupon = income when
  it is paid.
- Bank certificates of deposit and fixed term savings → `fixed_deposit` accounts (SKILL.md).
- Trái phiếu doanh nghiệp (corporate bonds): flag that it carries issuer risk if the user asks
  about safety; never present it as a deposit.

## Crypto

Crypto is not legally a payment or securities asset in Vietnam. Exchange wallets (Binance,
OKX, ...) are `investment` accounts; coins resolve through the normal coin feed and are priced
in USD, then converted. P2P buys of USDT with VND are a transfer into the exchange at the VND
amount paid. Tax treatment: do not state any; say it is not covered here.

## Dividends

- **Cash dividend** (cổ tức bằng tiền): income on the investment account. Personal income tax
  of 5% is withheld at source, so the cash received is net. Record the net amount received
  and, if the user wants the gross, the 5% as a separate row.
- **Stock dividend / bonus shares** (cổ tức bằng cổ phiếu, thưởng cổ phiếu): no cash. It adds
  shares at zero cost and the per-share cost basis drops; do not book income.
- Rights issues (quyền mua) are a purchase: ask for the price and the number of shares.

## Taxes and fees (Vietnam)

- Selling stocks or fund units: 0,1% of the sale value, withheld by the broker on each sell,
  on the whole proceeds, not on the profit.
- Cash dividends: 5% personal income tax (above).
- Broker commission (phí giao dịch): the broker's rate, so take it from the contract note, not a
  guess. Book it as a fee on the trade or as a separate fee row.
- Gold bar sales are not taxed as securities here; do not state a tax rate for gold.
- Rates change by law. When the user relies on a figure for a decision (tax filing), say these
  are the rates this skill knows and suggest checking the current regulation.

## Entering a trade

1. Make sure the instrument exists (`resolve_instruments`) and the brokerage account exists.
2. Record the buy or sell on the brokerage account with the trade date, quantity, price per
   share in VND, and the fee/tax rows from the contract note.
3. Reconcile the account's cash against the broker's statement balance.
