import pytest
from pandas.testing import assert_frame_equal
import geopandas as gp

from .context import gtfs_kit, DATA_DIR, cairns, cairns_shapeless
from gtfs_kit import *
from gtfs_kit import geometrize_shapes_0, ungeometrize_shapes_0


def test_append_dist_to_shapes():
    feed1 = cairns.copy()
    s1 = feed1.shapes
    feed2 = append_dist_to_shapes(feed1)
    s2 = feed2.shapes
    # Check that colums of st2 equal the columns of st1 plus
    # a shape_dist_traveled column
    cols1 = list(s1.columns.values) + ["shape_dist_traveled"]
    cols2 = list(s2.columns.values)
    assert set(cols1) == set(cols2)

    # Check that within each trip the shape_dist_traveled column
    # is monotonically increasing
    for name, group in s2.groupby("shape_id"):
        sdt = list(group["shape_dist_traveled"].values)
        assert sdt == sorted(sdt)


def test_geometrize_shapes_0():
    shapes = cairns.shapes.copy()
    geo_shapes = geometrize_shapes_0(shapes, use_utm=True)
    # Should be a GeoDataFrame
    assert isinstance(geo_shapes, gp.GeoDataFrame)
    # Should have the correct shape
    assert geo_shapes.shape[0] == shapes["shape_id"].nunique()
    assert geo_shapes.shape[1] == shapes.shape[1] - 2
    # Should have the correct columns
    expect_cols = set(list(shapes.columns) + ["geometry"]) - set(
        ["shape_pt_lon", "shape_pt_lat", "shape_pt_sequence", "shape_dist_traveled",]
    )
    assert set(geo_shapes.columns) == expect_cols


def test_ungeometrize_shapes_0():
    shapes = cairns.shapes.copy()
    geo_shapes = geometrize_shapes_0(shapes)
    shapes2 = ungeometrize_shapes_0(geo_shapes)
    # Test columns are correct
    expect_cols = set(list(shapes.columns)) - set(["shape_dist_traveled"])
    assert set(shapes2.columns) == expect_cols
    # Data frames should agree on certain columns
    cols = ["shape_id", "shape_pt_lon", "shape_pt_lat"]
    assert_frame_equal(shapes2[cols], shapes[cols])


def test_geometrize_shapes():
    g_1 = geometrize_shapes(cairns, use_utm=True)
    g_2 = geometrize_shapes_0(cairns.shapes, use_utm=True)
    assert g_1.equals(g_2)
    with pytest.raises(ValueError):
        geometrize_shapes(cairns_shapeless)


def test_build_geometry_by_shape():
    d = build_geometry_by_shape(cairns)
    assert isinstance(d, dict)
    assert len(d) == cairns.shapes.shape_id.nunique()


def test_shapes_to_geojson():
    feed = cairns.copy()
    shape_ids = feed.shapes.shape_id.unique()[:2]
    collection = shapes_to_geojson(feed, shape_ids)
    assert isinstance(collection, dict)
    assert len(collection["features"]) == len(shape_ids)

    with pytest.raises(ValueError):
        shapes_to_geojson(cairns_shapeless)


def test_get_shapes_intersecting_geometry():
    feed = cairns.copy()
    path = DATA_DIR / "cairns_square_stop_750070.geojson"
    polygon = sg.shape(json.load(path.open())["features"][0]["geometry"])
    pshapes = get_shapes_intersecting_geometry(feed, polygon)
    shape_ids = ["120N0005", "1200010", "1200001"]
    assert set(pshapes["shape_id"].unique()) == set(shape_ids)
