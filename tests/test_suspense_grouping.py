"""Grouping must preserve the evidence needed to reconsider suspense."""

import pytest

from scripts.book_local_mappings import merchant_group


@pytest.mark.parametrize('description', [
    '7-ELEVEN',
    '99 RANCH MARKET',
    '168 ASIAN MART',
    '21ST CENTURY INSURANCE',
    'STUDIO 54',
    '24 HOUR FITNESS',
    'B12 VITAMIN SHOP',
    'ORDER 66 COLLECTIBLES',
    'REFERENCE BOOKS',
    'VENMO PAYMENT TO ALICE42',
    'PAYPAL *STORE123',
    'AMZN*PRIME2024',
    'AMAZON PRIME*AB12CD34',
    'ONLINE TRANSFER TO SAVINGS 1234',
])
def test_meaningful_digits_and_merchant_details_survive(description):
    assert merchant_group(description) == description


def test_numeric_merchants_never_collapse_into_unknown_or_each_other():
    descriptions = ['7-ELEVEN', '99 RANCH MARKET', '168 ASIAN MART', '3M']
    groups = {merchant_group(value) for value in descriptions}
    assert len(groups) == len(descriptions)
    assert 'UNKNOWN' not in groups


def test_amazon_services_and_retail_do_not_share_a_category_group():
    descriptions = [
        'AMAZON.COM*AB12CD34E',
        'AMZN MKTP US*XY98ZW76V',
        'AMAZON WEB SERVICES',
        'AMAZON PRIME',
        'AMAZON MUSIC',
        'AMAZON KINDLE',
        'AMAZON PHARMACY',
        'AMAZON GROCERY SUBSCRI',
        'AMAZON KIDS+',
        'AMAZON KIDS+*AB12CD34E',
        'AMAZON TIPS*AB12CD34E',
    ]
    assert len({merchant_group(value) for value in descriptions}) == len(descriptions)


@pytest.mark.parametrize('first,second', [
    ('VENMO PAYMENT TO ALICE', 'VENMO PAYMENT TO BOB'),
    ('ZELLE PAYMENT TO ALICE', 'ZELLE PAYMENT FROM ALICE'),
    ('PAYPAL *ALICE123', 'PAYPAL *BOB45678'),
    ('ONLINE TRANSFER TO SAVINGS 1234', 'ONLINE TRANSFER TO SAVINGS 5678'),
    ('H-E-B STORE #1234', 'H-E-B GAS/CARWASH #1234'),
    ('AMAZON PRIME PAYMENT', 'AMAZON PRIME REFUND'),
])
def test_recipient_service_direction_and_destination_remain_distinct(first, second):
    assert merchant_group(first) != merchant_group(second)


@pytest.mark.parametrize('first,second,expected', [
    ('AMAZON.COM*AB12CD34E', 'AMAZON.COM*XY98ZW76V', 'AMAZON.COM'),
    ('AMAZON MKTPL*AB12CD34E', 'AMAZON MKTPL*XY98ZW76V', 'AMAZON MKTPL'),
    ('AMZN MKTP US*AB12CD34E', 'AMZN MKTP US*XY98ZW76V', 'AMZN MKTP US'),
    ('LOCAL DENTAL REFERENCE: A123', 'LOCAL DENTAL REFERENCE: B456', 'LOCAL DENTAL'),
    ('ACME PAYROLL PPD ID:123456', 'ACME PAYROLL PPD ID:654321', 'ACME PAYROLL'),
    ('99 RANCH STORE #123', '99 RANCH STORE #456', '99 RANCH STORE'),
])
def test_only_identified_reference_or_store_suffixes_group(first, second, expected):
    assert merchant_group(first) == merchant_group(second) == expected


def test_ach_metadata_does_not_discard_following_payment_purpose():
    description = 'ACME PPD ID:123456 WEB HOSTING'
    assert merchant_group(description) == description
    assert merchant_group(description) != merchant_group('ACME PPD ID:123456 LOAN PAYMENT')


def test_reference_suffix_parser_is_bounded_and_preserves_other_text():
    assert merchant_group('UTILITY TRACE: A123 REF: B456') == 'UTILITY'
    for description in [
        'TRACE: 1234',
        'STORE #1234',
        'SHOP REFERENCE: PAYMENT',
        'SHOP REF: A123/B456',
        'SHOP REF: A123 MERCHANT',
        'SHOP REF: ' + 'A1' * 40,
    ]:
        assert merchant_group(description) == description


def test_only_cosmetic_normalization_is_applied_to_other_descriptions():
    assert merchant_group('  cafe\t &amp;   bakery  ') == 'CAFE & BAKERY'
    assert merchant_group('*** 1234') == '*** 1234'
    assert merchant_group('   ') == 'UNKNOWN'
