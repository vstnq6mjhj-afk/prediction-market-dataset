# Phase 18 — Company identity and Manifold suspension

## 1. Copy files

Copy the package contents into the repository root so these overwrite/create:

- `api/source_policy.py`
- `api/routes/admin_data_health.py`
- `connectors/market_aggregator.py`
- `tests/test_source_policy.py`
- `apply_phase18_company_identity.py`
- `verify_phase18.py`

Do not copy company records, addresses, `.env`, or credentials into GitHub.

## 2. Apply the controlled main.py patch

```powershell
cd D:\prediction_market_dataset
python apply_phase18_company_identity.py
```

## 3. Configure local verification environment

```powershell
$env:COMPANY_LEGAL_NAME="PMD Data Systems Ltd"
$env:COMPANY_NUMBER="17347262"
$env:COMPANY_JURISDICTION="England and Wales"
$env:COMPANY_TRADING_NAME="Prediction Market Dataset"

$env:CUSTOMER_API_PLATFORMS=""
$env:EXPLORER_DATA_PLATFORMS=""
$env:CUSTOMER_EXPORT_PLATFORMS=""
$env:CUSTOMER_MATCHER_PLATFORMS=""
$env:PUBLIC_SUMMARY_PLATFORMS=""
$env:INTERNAL_DATA_PLATFORMS="kalshi,polymarket,predictit"

$env:BILLING_CHECKOUT_ENABLED="false"
$env:BILLING_TEST_MODE_ONLY="true"
$env:BILLING_REQUIRE_COMMERCIAL_SOURCES="true"
$env:BILLING_VISIBLE_TERMS="monthly"
```

Do not set the registered-office address in the repository. Add it directly in Render later.

## 4. Compile and test

```powershell
python -m py_compile api\main.py api\source_policy.py apioutesdmin_data_health.py connectors\market_aggregator.py verify_phase18.py
python -m unittest tests.test_source_policy -v
python verify_phase18.py
python verify_source_policy.py
```

## 5. Review changes

```powershell
git status
git diff -- api/main.py api/source_policy.py api/routes/admin_data_health.py connectors/market_aggregator.py tests/test_source_policy.py verify_phase18.py
```

Confirm no registered-office address, secret, `.env`, authentication code, UTR, or identity record appears.

## 6. Commit

```powershell
git add .gitignore api/main.py api/source_policy.py api/routes/admin_data_health.py connectors/market_aggregator.py tests/test_source_policy.py apply_phase18_company_identity.py verify_phase18.py
git commit -m "Add company identity and suspend Manifold collection"
git push -u origin phase18-company-and-manifold-cleanup
```

Merge the branch through GitHub after tests pass, or push to `main` only if that is your established deployment workflow.

## 7. Render environment

Add privately in Render:

```text
COMPANY_LEGAL_NAME=PMD Data Systems Ltd
COMPANY_NUMBER=17347262
COMPANY_JURISDICTION=England and Wales
COMPANY_TRADING_NAME=Prediction Market Dataset
COMPANY_REGISTERED_OFFICE=<registered office>
COMPANY_CONTACT_EMAIL=<monitored email>

CUSTOMER_API_PLATFORMS=
EXPLORER_DATA_PLATFORMS=
CUSTOMER_EXPORT_PLATFORMS=
CUSTOMER_MATCHER_PLATFORMS=
PUBLIC_SUMMARY_PLATFORMS=
INTERNAL_DATA_PLATFORMS=kalshi,polymarket,predictit

BILLING_CHECKOUT_ENABLED=false
BILLING_TEST_MODE_ONLY=true
BILLING_REQUIRE_COMMERCIAL_SOURCES=true
BILLING_VISIBLE_TERMS=monthly
```

## 8. Production verification

```bash
cd /opt/render/project/src
./.venv/bin/python verify_phase18.py
./.venv/bin/python verify_source_policy.py
```

Then verify:

- Website footer names PMD Data Systems Ltd and company number 17347262.
- `/terms` and `/privacy` identify the company.
- `/admin/data-health` shows Manifold as declined/prohibited.
- Connector diagnostics show Manifold terminated as `disabled_by_internal_source_policy` after the next fast and discovery runs.
- Customer allowlists remain empty.
- Checkout remains disabled.
- Kalshi, Polymarket, and PredictIt collection continues.

Existing Manifold warehouse rows are not deleted by this phase. They remain blocked from customer-facing surfaces and should remain restricted pending retention/deletion clarification.
