from sql_batch import fetch_primary_result_set


class _FakeCursor:
    def __init__(self, sets):
        self._sets = list(sets)
        self._idx = -1
        self.description = None

    def fetchall(self):
        if self._idx < 0:
            return []
        return self._sets[self._idx][1]

    def nextset(self):
        self._idx += 1
        if self._idx >= len(self._sets):
            self.description = None
            return False
        self.description = self._sets[self._idx][0]
        return True


def test_skips_empty_first_result_set():
    cur = _FakeCursor(
        [
            ([("x",)], []),
            ([("Sku",), ("Qty",)], [("996-1", 3), ("996-2", 1)]),
        ]
    )
    cur._idx = 0
    cur.description = cur._sets[0][0]
    cols, rows, ok = fetch_primary_result_set(cur)
    assert ok
    assert cols == ["Sku", "Qty"]
    assert len(rows) == 2


def test_declare_style_no_description_then_select():
    cur = _FakeCursor(
        [
            (None, []),
            ([("n",)], [(42,)]),
        ]
    )
    cur._idx = 0
    cur.description = None
    cols, rows, ok = fetch_primary_result_set(cur)
    assert ok
    assert cols == ["n"]
    assert rows == [(42,)]


def test_single_empty_select():
    cur = _FakeCursor([([("a",)], [])])
    cur._idx = 0
    cur.description = cur._sets[0][0]
    cols, rows, ok = fetch_primary_result_set(cur)
    assert ok
    assert cols == ["a"]
    assert rows == []
