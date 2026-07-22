# SCE setup

Use the current SCE bill to find the exact rate-plan code and meter-read/billing-cycle dates. Redact name, service address, account number, meter number, QR/bar codes, and payment information before sharing an image.

Choose the matching bundled plan, inspect its effective and checked dates, open the official source, and compare every displayed price. Public rates can change. If different, clone a new version with the new effective date and source notes; do not edit a version already used in a finalized report.

Enter a monthly baseline allocation only when verified from the account and only attach it to an explicitly complete `full_account` aggregate. A branch/one-CT device remains `energy_only`. Select SCE generation or configure CCA/Direct Access separately so generation is not double charged. Enter CARE/FERA, taxes, climate credits, and other bill adjustments only when known.

Create the persisted account under **Administration > Sites & accounts**. Select the published
rate version and an effective date rather than treating a library plan's published state as an
assignment. For CCA, SCE normally remains the delivery utility while the named community choice
provider supplies generation; for Direct Access, record the reviewed generation contract. Every
additional price or credit needs a provenance label and effective window. The server calculates
the current/next TOU period from account time and the assignment even before a sensor reports.

Run the preview calculator on representative summer/winter weekday and weekend intervals before activation. Confirm coverage and aggregate topology. The result is estimated monitored energy cost or estimated account cost, never an enrollment/change request to SCE and never a utility bill.
