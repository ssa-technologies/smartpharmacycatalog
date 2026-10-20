# Smart Pharmacy Catalog

Regional medicine catalogs for the **Smart Pharmacy POS** app. The app downloads these files straight from this
repository, so a new medicine reaches every shop with one tap on **Fetch New Medicines**, no app update needed.

| Region | Countries | Base catalog | Updates (delta) |
|---|---|---|---|
| `PK` | Pakistan | `catalog_pk_base.csv` | `catalog_pk_updates.csv` |
| `GCC` | Saudi Arabia, UAE, Qatar, Kuwait, Bahrain, Oman | `catalog_gcc_base.csv` | `catalog_gcc_updates.csv` |
| `IN` | India | `catalog_in_base.csv` | `catalog_in_updates.csv` |
| `UK_US` | United Kingdom, United States | `catalog_uk_us_base.csv` | `catalog_uk_us_updates.csv` |

* **Base** = the full starter catalog of a region. The app downloads it once (**Download Full Region Catalog**).
* **Updates** = only new / changed medicines. The app downloads this small file in about a second (**Fetch New Medicines**).
  Rows are matched by `code` (or by name + company) and *added or updated*; the shop's own stock and its own
  custom medicines are never touched.
* `version.json` tells the app which version is online, so it only downloads when something changed.

App base URL: `https://raw.githubusercontent.com/ssa-technologies/smartpharmacycatalog/main/`
(fallback mirror: `https://cdn.jsdelivr.net/gh/ssa-technologies/smartpharmacycatalog@main/`)

## What is in the base catalogs, and where it comes from

The app itself is tiny: it ships only a small starter list. Each shop downloads the base catalog of its own region once.
Rebuild them with `python scripts/harvest_all_regions.py` (see the header of that script).

| File | Rows | Source |
|---|---|---|
| `catalog_pk_base.csv` | about 41,800 | The DRAP-based Pakistan list that used to be bundled in the app (names, companies, pack sizes and prices as shipped) + about 300 Pakistani FMCG, baby milk, diaper, Hamdard and Qarshi items |
| `catalog_in_base.csv` | about 29,200 | [Indian Medicine Dataset](https://github.com/junioralive/Indian-Medicine-Dataset) (MIT licence), discontinued items removed, the big Indian pharma companies first, + curated OTC and baby items |
| `catalog_uk_us_base.csv` | about 31,300 | US FDA [openFDA NDC directory](https://open.fda.gov/) (public domain) + curated UK / US OTC brands |
| `catalog_gcc_base.csv` | about 5,900 | Curated Gulf brands + the generic formulary (INN name, strength, form) seen with two or more labelers in the openFDA data |

Honest limits:

* **No SFDA / MOHAP official list.** Those sites block automated downloads, so the GCC file is *not* an official registered-drug
  list. If you export the SFDA list yourself, put it into the Google Sheet and the sync adds it.
* **No UK NHS dm+d.** It needs a free NHS TRUD account to download. The UK part is the curated brand list only.
* **No invented data.** Prices (`mrp`) and barcodes are only filled where a real source has them; everything else is `0` / empty.
  The `GCC` (SAR) and `IN` curated brands carry *indicative* prices (see below); the Indian dataset prices are the dataset's own.
* Barcodes are never guessed: a made-up GTIN would make the POS scanner sell the wrong product.

## CSV format

First row (header) is exactly:

```
code,name,genericFormula,company,unitType,stripsPerBox,unitsPerBox,mrp,barcode
```

| Column | Meaning |
|---|---|
| `code` | Unique id, e.g. `GCC-0001`. Keep it stable: the same code later *updates* that medicine. Use `GCC-U006`... for new rows in an updates file. |
| `name` | Brand name with strength, e.g. `Panadol Extra Tab` (required) |
| `genericFormula` | Active ingredient(s), e.g. `Paracetamol 500mg` |
| `company` | Manufacturer / marketer (may be empty) |
| `unitType` | `Tablet`, `Capsule`, `Syrup`, `Injection`, `Cream`, `Drops`, `Sachet`, `Inhaler` or `Other` |
| `stripsPerBox` | Strips (pattay) in one box, `1` if unknown |
| `unitsPerBox` | Tablets / units in one box, `1` if unknown |
| `mrp` | Retail price of one box in the region's currency, `0` if unknown |
| `barcode` | EAN / GTIN if known, otherwise empty |

The app also reads common public-dataset headers (`medicine_name`, `generic_name`, `manufacturer_name`,
`price`, `dosage_form` ...) in any letter case.

> **About prices.** The `mrp` values of `GCC` (SAR) and `IN` (INR) are **indicative starting prices**, not an official
> price list. The `PK` prices come from the DRAP-based list that ships inside the app. `UK_US` has no prices
> (the region mixes GBP and USD). Shops always edit the sale price when they add stock. Replace the numbers with your
> own verified prices through the Google Sheet below.

## Update medicines from a Google Sheet (no coding)

1. Make a Google Sheet for a region. Row 1 must be the header above. One medicine per row.
2. In the sheet: **File > Share > Publish to web**, choose the sheet tab, choose **Comma-separated values (.csv)**,
   click **Publish** and copy the link.
3. Open `sheets_config.json` in this repository (GitHub: click the file, then the pencil icon) and paste the link between
   the quotes of that region:

   ```json
   {
     "PK_UPDATES_SHEET_CSV_URL": "",
     "GCC_UPDATES_SHEET_CSV_URL": "https://docs.google.com/spreadsheets/d/e/....../pub?output=csv",
     "IN_UPDATES_SHEET_CSV_URL": "",
     "UK_US_UPDATES_SHEET_CSV_URL": ""
   }
   ```

   Leave a region empty to skip it. Commit the change.
4. Go to **Actions > Sync Google Sheets > Run workflow** (1 click). The workflow also checks the sheets by itself every
   6 hours.

What the workflow does for every region that has a link:

1. downloads the published CSV into `catalog_<region>_updates.csv`,
2. checks that it is real CSV with a header row containing `name` (an unpublished sheet returns an HTML page and is rejected),
3. if the content changed: increments that region's `updates_version` and refreshes `last_updated` in `version.json`,
4. commits and pushes to `main`. If nothing changed there is no commit.

Phones then see the higher `updates_version` and download the new rows on the next **Fetch New Medicines**.

Run the same script on your own PC to test a link: `python scripts/sync_sheets.py`.

## The official sheets (already connected)

`sheets_config.json` already holds the four published tabs of the official Google Sheet (PK, GCC, IN, UK_US). The first
time the workflow runs it copies each tab into `catalog_<region>_updates.csv`, so the sample rows that ship in those
files are replaced by whatever is in the sheet (a tab with only the header row gives an empty updates file).
The app also knows these four links: when GitHub is not reachable, **Fetch New Medicines** reads the sheet directly.

## Changing the base catalog

Edit `catalog_<region>_base.csv`, then raise `base_version` of that region in `version.json` (so phones know to download
it again).

## Files

```
catalog_*_base.csv / catalog_*_updates.csv   the catalogs
version.json                                  versions + timestamps for PK, GCC, IN, UK_US
sheets_config.json                            Google Sheets "Publish to web" CSV links
scripts/sync_sheets.py                        the sync used by the workflow
.github/workflows/sync_google_sheets.yml      manual + every-6-hours sync
```

## اردو میں خلاصہ

* گوگل شیٹ میں دوائیں لکھیں، **Publish to web → CSV** کا لنک `sheets_config.json` میں اپنے ملک کے سامنے پیسٹ کریں۔
* پھر GitHub کے **Actions → Sync Google Sheets → Run workflow** پر کلک کریں۔ نئی دوائیں خود بخود `catalog_*_updates.csv` میں آ جائیں گی۔
* دکاندار ایپ میں **Fetch New Medicines** دبائیں تو نئی دوائیں ایک سیکنڈ میں آ جاتی ہیں۔
