from t2s.compare import results_match


def test_order_ignored():
    assert results_match([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])


def test_float_rounding():
    assert results_match([(2328.6,)], [(2328.6000000000004,)])
    assert results_match([(10,)], [(10.0,)])


def test_extra_and_reordered_columns():
    assert results_match([("Rock",)], [("Rock", 1297)])
    assert results_match([("USA", 523.06)], [(523.06, "USA", 13)])


def test_mismatches():
    assert not results_match([(1,)], [(2,)])
    assert not results_match([(1,), (2,)], [(1,)])
    assert not results_match([("a", 1)], [("a",)])
    # same column values but rows paired differently
    assert not results_match([("a", 1), ("b", 2)], [("a", 2), ("b", 1)])
