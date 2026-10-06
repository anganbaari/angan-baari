'use strict';
/* Bikram Sambat (B.S.) calendar helpers for the Reports dashboard
 * (/dashboard/) client-side date picker -- instant preset clicks without
 * a server round trip. This mirrors shop/bs_calendar.py's data table
 * (itself ported from templates/pos.html's inline "Clock" converter) --
 * THREE copies of the same table now exist (pos.html inline, this file,
 * shop/bs_calendar.py); if the table is ever extended past BS 2099 or
 * corrected, update all three. shop/bs_calendar.py is the one with
 * Django-test coverage; this file exists purely for the dashboard UI's
 * own responsiveness and carries no server-side authority -- every date
 * this picker produces is sent to the existing /api/v1/reports/*
 * endpoints as a plain A.D. ISO string, same as before.
 *
 * SOURCE: remotemerge/nepali-date-converter (MIT), years.ts, BS 2075-2099.
 */

const BsCalendar = (() => {
    const BS_EPOCH_YEAR = 2075;
    const BS_EPOCH_AD = Date.UTC(2018, 3, 14); // 2018-04-14

    const NEPALI_CALENDAR_DATA = {
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
    };
    const MAX_BS_YEAR = 2099;

    const MONTH_NAMES = ['बैशाख', 'जेठ', 'असार', 'साउन', 'भदौ', 'असोज', 'कार्तिक', 'मंसिर', 'पुष', 'माघ', 'फागुन', 'चैत'];
    const DAY_NAMES = ['आइत', 'सोम', 'मंगल', 'बुध', 'बिही', 'शुक्र', 'शनि'];
    const SHRAWAN_MONTH = 4;

    function utcDate(y, m, d) { return new Date(Date.UTC(y, m, d)); }

    // date (JS Date, local wall-clock fields used as the "A.D. calendar day") -> {year, month, day} (B.S., 1-indexed)
    function adToBs(jsDate) {
        const utcMidnight = Date.UTC(jsDate.getFullYear(), jsDate.getMonth(), jsDate.getDate());
        let daysSinceEpoch = Math.floor((utcMidnight - BS_EPOCH_AD) / 86400000);
        let bsYear = BS_EPOCH_YEAR, bsMonth = 0;
        while (true) {
            const monthLengths = NEPALI_CALENDAR_DATA[bsYear];
            if (!monthLengths) throw new Error(`BS date out of range past ${MAX_BS_YEAR}`);
            if (bsMonth >= 12) { bsYear += 1; bsMonth = 0; continue; }
            const len = monthLengths[bsMonth];
            if (daysSinceEpoch < len) break;
            daysSinceEpoch -= len;
            bsMonth += 1;
        }
        return { year: bsYear, month: bsMonth + 1, day: daysSinceEpoch + 1 };
    }

    function bsMonthLength(year, month) {
        const lengths = NEPALI_CALENDAR_DATA[year];
        if (!lengths) throw new Error(`BS year ${year} out of range`);
        return lengths[month - 1];
    }

    // {year, month, day} (B.S.) -> JS Date (UTC midnight, used as a plain calendar day)
    function bsToAd(year, month, day) {
        if (year < BS_EPOCH_YEAR || year > MAX_BS_YEAR) throw new Error(`BS year ${year} out of range`);
        let daysBefore = 0;
        for (let y = BS_EPOCH_YEAR; y < year; y++) {
            daysBefore += NEPALI_CALENDAR_DATA[y].reduce((a, b) => a + b, 0);
        }
        const lengths = NEPALI_CALENDAR_DATA[year];
        for (let m = 0; m < month - 1; m++) daysBefore += lengths[m];
        daysBefore += day - 1;
        return new Date(BS_EPOCH_AD + daysBefore * 86400000);
    }

    function bsAddMonths(year, month, delta) {
        const zeroBased = (year - BS_EPOCH_YEAR) * 12 + (month - 1) + delta;
        const newYear = BS_EPOCH_YEAR + Math.floor(zeroBased / 12);
        const newMonth = ((zeroBased % 12) + 12) % 12 + 1;
        return { year: newYear, month: newMonth };
    }

    function bsMonthStartAd(year, month) { return bsToAd(year, month, 1); }
    function bsMonthEndAd(year, month) { return bsToAd(year, month, bsMonthLength(year, month)); }

    function bsFiscalYearStart(jsDate) {
        const bs = adToBs(jsDate);
        const fiscalYear = bs.month >= SHRAWAN_MONTH ? bs.year : bs.year - 1;
        return bsMonthStartAd(fiscalYear, SHRAWAN_MONTH);
    }

    function toYmd(jsDate) {
        return `${jsDate.getUTCFullYear()}-${String(jsDate.getUTCMonth() + 1).padStart(2, '0')}-${String(jsDate.getUTCDate()).padStart(2, '0')}`;
    }

    function formatBs(bs, withYear) {
        const label = `${bs.day} ${MONTH_NAMES[bs.month - 1]}`;
        return withYear === false ? label : `${label} ${bs.year}`;
    }

    function todayBs() { return adToBs(new Date()); }

    return {
        MONTH_NAMES, DAY_NAMES, BS_EPOCH_YEAR, MAX_BS_YEAR,
        adToBs, bsToAd, bsMonthLength, bsAddMonths, bsMonthStartAd, bsMonthEndAd,
        bsFiscalYearStart, toYmd, formatBs, todayBs, utcDate,
    };
})();
