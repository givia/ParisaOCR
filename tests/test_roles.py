"""The line-role models: the tree evaluator, and the bundled models' shape."""
import numpy as np


def test_forest_routes_like_the_trees(tmp_path):
    """One tree, split x0 <= 0.5: left leaf -2, right leaf +2; a missing value goes right."""
    from parisaocr.ebook.roles import Forest
    np.savez(tmp_path / "note.npz", names=np.array(["x0"]), classes=np.array([0, 1]), baseline=np.array([0.5]), k=1,
             feature=np.array([[0, 0, 0]]), threshold=np.array([[0.5, 0.0, 0.0]]),
             left=np.array([[1, 0, 0]]), right=np.array([[2, 0, 0]]), missing_left=np.array([[False, False, False]]),
             value=np.array([[0.0, -2.0, 2.0]]), leaf=np.array([[False, True, True]]))
    f = Forest(tmp_path / "note.npz")
    raw = f.raw(np.array([[0.2], [0.9], [np.nan]], dtype=np.float32))[:, 0]
    assert raw.tolist() == [-1.5, 2.5, 2.5]
    p = f.proba(np.array([[0.2], [0.9]]))
    assert np.allclose(p[:, 0] + p[:, 1], 1) and p[0, 1] < 0.5 < p[1, 1]
    assert f.predict(np.array([[0.2], [0.9]])).tolist() == [0, 1]


def test_bundled_models():
    from parisaocr.ebook.roles import BUNDLED, MODELS, NAMES, Forest, describe
    for name, _ in MODELS:
        f = Forest(BUNDLED / f"{name}.npz")
        assert f.names == NAMES, name
        assert f.k == (1 if name in ("note", "heading", "start") else len(f.classes))
    role = Forest(BUNDLED / "role.npz")
    assert role.labels[role.classes.index(6)] == "note" and len(role.labels) == 13
    p = role.proba(np.zeros((2, len(NAMES))))
    assert p.shape == (2, 13) and np.allclose(p.sum(axis=1), 1)
    assert describe(BUNDLED).startswith("models ")
