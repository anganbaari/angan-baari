"""Bikram Sambat (B.S., Nepali calendar) <-> Gregorian (A.D.) conversion.

This is the Python side of the SAME data table templates/pos.html already
carries inline (its "Clock — Bikram Sambat (BS) date" section) — ported,
not independently re-derived, so the two can't silently disagree about
what date a given day falls on. pos.html's own copy is left untouched
(it's working, tested, load-bearing POS code — out of scope for the
Reports dashboard task that created this file) rather than refactored to
import from here, so **if this table is ever extended or corrected,
update both this file and templates/pos.html's NEPALI_CALENDAR_DATA**. A
third, smaller copy lives in static/js/bs-calendar.js for the Reports
dashboard's own client-side date picker (instant preset clicks without a
round trip) — same three-way sync obligation.

SOURCE: extracted from remotemerge/nepali-date-converter (MIT license,
https://github.com/remotemerge/nepali-date-converter, file
html/src/years.ts, fetched 2026-09-30). Only the BS 2075-2099 slice is
kept (AD ~2018-04-14 to ~2044-04-12) — this project only ever needs
"recent/current" dates, not historical ones. Verified against
hamropatro.com for several reference points (today 2026-09-30 AD = BS
2083 Ashoj 14; BS 2083-01-01 = 2026-04-14 AD; see pos.html's comment for
the full verification log).

Unlike pos.html's JS version (which silently breaks out of its loop and
returns a stale/wrong result past the end of the table), every function
here raises BSDateOutOfRangeError for a date outside the covered range --
failing loudly is more appropriate for server-side code whose output
feeds aggregation/reporting, not just a clock display.
"""
from datetime import date, timedelta

BS_EPOCH_YEAR = 2075  # 2075/01/01 BS = 2018-04-14 AD
BS_EPOCH_AD = date(2018, 4, 14)

NEPALI_CALENDAR_DATA = {
    2075: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2076: [31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 30],
    2077: [31, 32, 31, 32, 31, 30, 30, 30, 29, 30, 29, 31],
    2078: [31, 31, 31, 32, 31, 31, 30, 29, 30, 29, 30, 30],
    2079: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2080: [31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 30],
    2081: [31, 32, 31, 32, 31, 30, 30, 30, 29, 30, 29, 31],
    2082: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2083: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2084: [31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 31],
    2085: [30, 32, 31, 32, 31, 30, 30, 30, 29, 30, 29, 31],
    2086: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2087: [31, 31, 32, 32, 31, 30, 30, 29, 30, 29, 30, 30],
    2088: [31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 31],
    2089: [30, 32, 31, 32, 31, 30, 30, 30, 29, 30, 29, 31],
    2090: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2091: [31, 31, 32, 32, 31, 30, 30, 29, 30, 29, 30, 30],
    2092: [31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 31],
    2093: [31, 31, 31, 32, 31, 31, 29, 30, 30, 29, 29, 31],
    2094: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2095: [31, 31, 32, 32, 31, 30, 30, 29, 30, 29, 30, 30],
    2096: [31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 31],
    2097: [31, 31, 31, 32, 31, 31, 29, 30, 30, 29, 30, 30],
    2098: [31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30],
    2099: [31, 31, 32, 32, 31, 30, 30, 29, 30, 29, 30, 30],
}

MIN_BS_YEAR = min(NEPALI_CALENDAR_DATA)
MAX_BS_YEAR = max(NEPALI_CALENDAR_DATA)

BS_MONTH_NAMES = ['बैशाख', 'जेठ', 'असार', 'साउन', 'भदौ', 'असोज', 'कार्तिक', 'मंसिर', 'पुष', 'माघ', 'फागुन', 'चैत']
BS_MONTH_NAMES_EN = [
    'Baishakh', 'Jestha', 'Ashar', 'Shrawan', 'Bhadra', 'Ashoj',
    'Kartik', 'Mangsir', 'Poush', 'Magh', 'Falgun', 'Chaitra',
]
SHRAWAN_MONTH = 4  # fiscal year start


class BSDateOutOfRangeError(ValueError):
    """Raised for any A.D./B.S. date outside BS_EPOCH_YEAR..MAX_BS_YEAR+1 --
    the table this module (and pos.html's own copy) was built from doesn't
    cover it. See this module's docstring for how to extend the table."""


def ad_to_bs(ad_date):
    """date (A.D.) -> (bs_year, bs_month, bs_day), month/day both 1-indexed."""
    if ad_date < BS_EPOCH_AD:
        raise BSDateOutOfRangeError(f'{ad_date} is before the earliest date this table covers ({BS_EPOCH_AD}).')
    days_since_epoch = (ad_date - BS_EPOCH_AD).days
    bs_year, bs_month = BS_EPOCH_YEAR, 0
    while True:
        month_lengths = NEPALI_CALENDAR_DATA.get(bs_year)
        if month_lengths is None:
            raise BSDateOutOfRangeError(
                f'{ad_date} converts past BS {MAX_BS_YEAR} -- the table needs extending (see this module\'s docstring).'
            )
        if bs_month >= 12:
            bs_year += 1
            bs_month = 0
            continue
        length = month_lengths[bs_month]
        if days_since_epoch < length:
            break
        days_since_epoch -= length
        bs_month += 1
    return bs_year, bs_month + 1, days_since_epoch + 1


def bs_month_length(bs_year, bs_month):
    """Number of days in the given B.S. month (1-indexed)."""
    month_lengths = NEPALI_CALENDAR_DATA.get(bs_year)
    if month_lengths is None:
        raise BSDateOutOfRangeError(f'BS year {bs_year} is outside {MIN_BS_YEAR}-{MAX_BS_YEAR}.')
    if not (1 <= bs_month <= 12):
        raise ValueError(f'bs_month must be 1-12, got {bs_month}.')
    return month_lengths[bs_month - 1]


def bs_to_ad(bs_year, bs_month, bs_day):
    """(bs_year, bs_month, bs_day) -> date (A.D.). Inverse of ad_to_bs() --
    not needed by pos.html's clock (display-only, AD->BS is enough there),
    but required here for turning a BS-based preset ("this BS month") into
    the AD start/end the existing API endpoints take."""
    if bs_year < BS_EPOCH_YEAR or bs_year > MAX_BS_YEAR:
        raise BSDateOutOfRangeError(f'BS year {bs_year} is outside {BS_EPOCH_YEAR}-{MAX_BS_YEAR}.')
    if not (1 <= bs_month <= 12):
        raise ValueError(f'bs_month must be 1-12, got {bs_month}.')
    max_day = bs_month_length(bs_year, bs_month)
    if not (1 <= bs_day <= max_day):
        raise ValueError(f'bs_day must be 1-{max_day} for BS {bs_year}-{bs_month:02d}, got {bs_day}.')

    days_before = 0
    year = BS_EPOCH_YEAR
    while year < bs_year:
        days_before += sum(NEPALI_CALENDAR_DATA[year])
        year += 1
    days_before += sum(NEPALI_CALENDAR_DATA[bs_year][:bs_month - 1])
    days_before += bs_day - 1
    return BS_EPOCH_AD + timedelta(days=days_before)


def bs_month_start_ad(bs_year, bs_month):
    return bs_to_ad(bs_year, bs_month, 1)


def bs_month_end_ad(bs_year, bs_month):
    return bs_to_ad(bs_year, bs_month, bs_month_length(bs_year, bs_month))


def bs_add_months(bs_year, bs_month, delta):
    """(bs_year, bs_month) shifted by `delta` whole B.S. months (may be negative)."""
    zero_based = (bs_year - BS_EPOCH_YEAR) * 12 + (bs_month - 1) + delta
    new_year = BS_EPOCH_YEAR + zero_based // 12
    new_month = zero_based % 12 + 1
    return new_year, new_month


def bs_fiscal_year_start(ad_date):
    """The A.D. date of Shrawan 1 (B.S. month 4 -- Nepal's fiscal year
    start) for the fiscal year containing `ad_date`."""
    bs_year, bs_month, _ = ad_to_bs(ad_date)
    fiscal_bs_year = bs_year if bs_month >= SHRAWAN_MONTH else bs_year - 1
    return bs_month_start_ad(fiscal_bs_year, SHRAWAN_MONTH)


def format_bs(bs_year, bs_month, bs_day, month_names=BS_MONTH_NAMES):
    return f'{bs_day} {month_names[bs_month - 1]} {bs_year}'
