from collections import Counter

from eg4tool.drivers.eg4_ll import DRIVER

ORDERED_KEYS, PAGES, SETTINGS = DRIVER.ordered_keys, DRIVER.pages, DRIVER.table


def test_every_setting_shown_exactly_once():
    counts = Counter(ORDERED_KEYS)
    assert set(counts) == set(SETTINGS)
    assert [k for k, n in counts.items() if n > 1] == []


def test_rows_fit_their_columns():
    for page in PAGES:
        for section in page.sections:
            for label, keys in section.rows:
                assert len(keys) == len(section.columns), (page.title, section.title, label)


def test_matrix_rows_share_a_scale():
    # A row's cells are compared against each other by ORDER, so they must
    # all be the same kind of value.
    for page in PAGES:
        for section in page.sections:
            if section.columns[0] != "Warning":
                continue
            for _label, keys in section.rows:
                assert len({(SETTINGS[k].multiplier, SETTINGS[k].offset) for k in keys}) == 1
                assert keys[0].endswith("_warning") and keys[1].endswith("_protection") and keys[2].endswith("_release")
