"""Tests for shop/bs_calendar.py. Reference points are the same ones
already verified (against hamropatro.com) in templates/pos.html's own
comment log for its inline copy of this same data table -- reusing them
here checks this Python port agrees with that JS original, not just with
itself."""
from datetime import date

from django.test import SimpleTestCase

from shop.bs_calendar import (
    BSDateOutOfRangeError, ad_to_bs, bs_add_months, bs_fiscal_year_start,
    bs_month_end_ad, bs_month_length, bs_month_start_ad, bs_to_ad, format_bs,
)


class AdToBsReferencePointsTests(SimpleTestCase):
    def test_today_2026_09_30_is_bs_2083_ashoj_14(self):
        self.assertEqual(ad_to_bs(date(2026, 9, 30)), (2083, 6, 14))

    def test_bs_new_year_2083_01_01_is_2026_04_14(self):
        self.assertEqual(ad_to_bs(date(2026, 4, 14)), (2083, 1, 1))

    def test_day_before_new_year_is_last_day_of_previous_bs_year(self):
        self.assertEqual(ad_to_bs(date(2026, 4, 13)), (2082, 12, 30))

    def test_ashoj_1_boundary(self):
        self.assertEqual(ad_to_bs(date(2026, 9, 17)), (2083, 6, 1))

    def test_kartik_1_boundary(self):
        self.assertEqual(ad_to_bs(date(2026, 10, 18)), (2083, 7, 1))


class BsToAdRoundTripTests(SimpleTestCase):
    def test_bs_to_ad_is_the_exact_inverse_of_ad_to_bs(self):
        for ad in [date(2026, 9, 30), date(2026, 4, 14), date(2026, 4, 13), date(2020, 1, 1), date(2030, 12, 31)]:
            bs_year, bs_month, bs_day = ad_to_bs(ad)
            self.assertEqual(bs_to_ad(bs_year, bs_month, bs_day), ad)

    def test_ad_to_bs_is_the_exact_inverse_of_bs_to_ad(self):
        for bs in [(2083, 6, 14), (2083, 1, 1), (2082, 12, 30), (2075, 1, 1), (2099, 12, 1)]:
            self.assertEqual(ad_to_bs(bs_to_ad(*bs)), bs)


class MonthBoundaryTests(SimpleTestCase):
    """The specific thing the dashboard brief asked to be tested: the
    last day of a B.S. month must convert to the day immediately before
    the first day of the next B.S. month, with no gap or overlap."""

    def test_last_day_of_month_is_immediately_before_first_day_of_next(self):
        for bs_year, bs_month in [(2083, 6), (2083, 12), (2082, 12), (2084, 1)]:
            next_year, next_month = bs_add_months(bs_year, bs_month, 1)
            last_day_of_this_month = bs_month_end_ad(bs_year, bs_month)
            first_day_of_next_month = bs_month_start_ad(next_year, next_month)
            self.assertEqual(first_day_of_next_month - last_day_of_this_month, date.resolution)

    def test_month_length_matches_actual_days_between_start_and_end(self):
        for bs_year, bs_month in [(2083, 6), (2083, 1), (2082, 12), (2085, 1)]:
            start = bs_month_start_ad(bs_year, bs_month)
            end = bs_month_end_ad(bs_year, bs_month)
            self.assertEqual((end - start).days + 1, bs_month_length(bs_year, bs_month))

    def test_year_boundary_chaitra_to_baishakh(self):
        """Chaitra (month 12) of one BS year rolls into Baishakh (month 1)
        of the next, not month 13 of the same year."""
        year, month = bs_add_months(2082, 12, 1)
        self.assertEqual((year, month), (2083, 1))


class BsAddMonthsTests(SimpleTestCase):
    def test_forward_within_year(self):
        self.assertEqual(bs_add_months(2083, 6, 1), (2083, 7))

    def test_forward_across_year_boundary(self):
        self.assertEqual(bs_add_months(2083, 12, 1), (2084, 1))

    def test_backward_across_year_boundary(self):
        self.assertEqual(bs_add_months(2083, 1, -1), (2082, 12))

    def test_zero_delta_is_identity(self):
        self.assertEqual(bs_add_months(2083, 6, 0), (2083, 6))


class FiscalYearTests(SimpleTestCase):
    """Nepal's fiscal year starts 1 Shrawan (B.S. month 4)."""

    def test_date_after_shrawan_1_uses_same_bs_year(self):
        # 2026-09-30 AD = BS 2083 Ashoj 14 -- after Shrawan 1 of 2083.
        fy_start = bs_fiscal_year_start(date(2026, 9, 30))
        self.assertEqual(ad_to_bs(fy_start), (2083, 4, 1))

    def test_date_before_shrawan_1_uses_previous_bs_year(self):
        # 2026-04-14 AD = BS 2083 Baishakh 1 -- before that year's Shrawan 1.
        fy_start = bs_fiscal_year_start(date(2026, 4, 14))
        self.assertEqual(ad_to_bs(fy_start), (2082, 4, 1))


class OutOfRangeTests(SimpleTestCase):
    def test_ad_date_before_epoch_raises(self):
        with self.assertRaises(BSDateOutOfRangeError):
            ad_to_bs(date(2018, 4, 13))

    def test_bs_year_before_epoch_raises(self):
        with self.assertRaises(BSDateOutOfRangeError):
            bs_to_ad(2074, 1, 1)

    def test_bs_year_after_table_raises(self):
        with self.assertRaises(BSDateOutOfRangeError):
            bs_to_ad(2100, 1, 1)

    def test_invalid_bs_day_for_short_month_raises(self):
        with self.assertRaises(ValueError):
            bs_to_ad(2083, 8, 30)  # BS 2083 month 8 (Mangsir) has only 29 days


class FormatBsTests(SimpleTestCase):
    def test_format_uses_devanagari_month_name_by_default(self):
        self.assertEqual(format_bs(2083, 6, 14), '14 असोज 2083')
