from decimal import Decimal


def format_money(value):
    """Whole-number prices display without a trailing '.00' ('80.00' -> '80'),
    but a real decimal amount keeps its full 2-place form ('80.50' stays
    '80.50', not '80.5'). Shared by the admin display and the POS templates'
    JS equivalent (formatMoney in pos.html) so both follow the same rule."""
    if value is None or value == '':
        return ''
    d = Decimal(value).quantize(Decimal('0.01'))
    if d == d.to_integral_value():
        return str(int(d))
    return f'{d:.2f}'
