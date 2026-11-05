# Smart Expense Claims: Calculation and Review

## What the member does

1. **Upload** a PDF or image of a flight ticket, hotel booking, train booking, invoice, or receipt.
2. **Check details** suggested by AI: traveller, dates, route, vendor, amount, currency, and document type. Correct any mistake and inspect the calculation.
3. **Confirm** the claim. The app opens a separate confirmation page with the saved total and a link to upload another document. A member may delete their own unconfirmed draft and uploaded original before confirmation.

AI supplies candidate facts only. It does not choose the rate, count payable days, convert money, or decide the total. The app calculates those values with fixed rules after the member checks the fields.

## Exact rule used for a new claim

The admin sets **one daily allowance rate in EUR**. It applies to flight, hotel, and train documents only. The rate is per calendar date, including the first and last travel dates. A document can cover 1 to 365 inclusive dates. A receipt, cafe bill, or ordinary invoice receives no allowance by itself, even if purchased during travel.

The app finds dates already paid on **confirmed travel claims owned by the same user account**. A date receives the daily allowance only once for that account. Overlapping documents can still contribute their separate booking costs. The traveller text on a ticket does not change which account owns the allowance. There is no monthly cap or remaining monetary balance: “used” means travel dates that have already received an allowance.

```text
travel days          = inclusive dates from first to last travel date
eligible days        = travel days minus dates paid on previous confirmed travel claims
allowance EUR        = eligible days × admin's EUR daily rate
booking EUR          = document amount × dated conversion rate to EUR
total claim EUR      = booking EUR + allowance EUR
```

The admin can turn off inclusion of the booking cost for travel documents. In that case booking EUR is 0 in the total, while eligible daily allowance still applies. Non-travel receipts always include their document amount. Amounts are calculated with decimal arithmetic and rounded to two digits after the decimal point.

For USD, EGP, and other supported currencies, the app fetches a recent dated EUR-based quote from ExchangeRate-API Open Access. If the service says `1 EUR = N` units of the source currency, the app uses `1 / N` EUR per source unit. It shows the quote, rate date, source amount, converted booking cost, allowance, and total before confirmation. It stores the applied rate and calculated total with the confirmed claim. If a recent quote is unavailable, a foreign-currency claim cannot be confirmed until the rate service works; the app does not invent a rate. EUR receipts need no exchange lookup.

| Example with EUR 35.50 per day | Calculation | Claim total |
|---|---:|---:|
| EUR 120 flight, 10–12 Nov, no previous dates | EUR 120 + 3 × EUR 35.50 | **EUR 226.50** |
| EUR 90 hotel, 11–12 Nov, after confirming that flight | EUR 90 + 0 × EUR 35.50 | **EUR 90.00** |
| EUR 8 cafe receipt on 11 Nov | EUR 8 + no daily allowance | **EUR 8.00** |
| USD 120 flight, 1 new day, **illustrative** 0.80 EUR/USD quote | EUR 96 + EUR 35.50 | **EUR 131.50** |

The last row is arithmetic practice only; the live rate will differ. If a second travel document has one new day and two previously paid days, it receives one day's allowance. A different team member's account can claim its own eligible dates. Changing the admin rate affects future confirmations, not the stored calculation for a confirmed claim.

## Where the code runs

| Step | Source |
|---|---|
| AI fact extraction prompt and upload/review flow | [`app/finance/prompts.py`](../app/finance/prompts.py), [`app/finance/routes.py`](../app/finance/routes.py) |
| Look up confirmed dates for this account, rate setting, and conversion quote | [`app/finance/routes.py`](../app/finance/routes.py) (`_claimed_travel_dates`, `_rule`, `_claim`) |
| Inclusive date limit, payable dates, allowance, booking conversion, rounding, total | [`app/finance/rules.py`](../app/finance/rules.py) (`calculate_days`, `calculate_claim`) |
| Fetch and validate dated exchange rates | [`app/integrations/fx.py`](../app/integrations/fx.py) (`get_eur_rate`) |
| Recheck rules and save calculation on confirmation | [`app/finance/routes.py`](../app/finance/routes.py) (`finance_confirm`) |
| Admin EUR rate and categories | [`app/core/admin_routes.py`](../app/core/admin_routes.py), `settings` in SQLite |

Confirmation rechecks the latest rate and already claimed dates. If either changed since the review screen opened, the app shows a refreshed calculation and asks the member to review it again. A suspected duplicate needs admin review. Confirmed claims are saved in SQLite and then sent to Google Sheets; a temporary Sheet failure does not erase the saved claim.

## Current limits

This is a simple account-based travel allowance, not a policy engine for country rates, meal deductions, half days, or annual budgets. Admins must approve the organization's reimbursement policy and review unusual travel. The AI can misread documents; the user must correct the facts before confirming.
