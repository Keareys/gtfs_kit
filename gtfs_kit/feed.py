"""
This module defines a Feed class to represent GTFS feeds.
There is an instance attribute for every GTFS table (routes, stops, etc.),
which stores the table as a Pandas DataFrame,
or as ``None`` in case that table is missing.

The Feed class also has heaps of methods: a method to compute route stats,
a method to compute screen line counts, validations methods, etc.
To ease reading, almost all of these methods are defined in other modules and
grouped by theme (``routes.py``, ``stops.py``, etc.).
These methods, or rather functions that operate on feeds, are
then imported within the Feed class.
This separation of methods unfortunately messes up slightly the ``Feed`` class
documentation generated by Sphinx, introducing an extra leading ``feed``
parameter in the method signatures.
Ignore that extra parameter; it refers to the Feed instance,
usually called ``self`` and usually hidden automatically by Sphinx.
"""
from pathlib import Path
import tempfile
import shutil
from copy import deepcopy
import zipfile
from typing import Optional, Union

import pandas as pd
from pandas.core.frame import DataFrame
import requests

from . import constants as cs
from . import helpers as hp
from . import cleaners as cn


class Feed(object):
    """
    An instance of this class represents a not-necessarily-valid GTFS feed,
    where GTFS tables are stored as DataFrames.
    Beware that the stop times DataFrame can be big (several gigabytes),
    so make sure you have enough memory to handle it.

    Primary instance attributes:

    - ``dist_units``: a string in :const:`.constants.DIST_UNITS`;
      specifies the distance units to use when calculating various
      stats, such as route service distance; should match the implicit
      distance units of the ``shape_dist_traveled`` column values,
      if present
    - ``agency``
    - ``stops``
    - ``routes``
    - ``trips``
    - ``stop_times``
    - ``calendar``
    - ``calendar_dates``
    - ``fare_attributes``
    - ``fare_rules``
    - ``shapes``
    - ``frequencies``
    - ``transfers``
    - ``feed_info``

    There are also a few secondary instance attributes that are derived
    from the primary attributes and are automatically updated when the
    primary attributes change.
    However, for this update to work, you must update the primary
    attributes like this (good)::

        feed.trips['route_short_name'] = 'bingo'
        feed.trips = feed.trips

    and **not** like this (bad)::

        feed.trips['route_short_name'] = 'bingo'

    The first way ensures that the altered trips DataFrame is saved as
    the new ``trips`` attribute, but the second way does not.

    """

    # Import heaps of methods from modules split by functionality;
    # i learned this trick from
    # https://groups.google.com/d/msg/comp.lang.python/goLBrqcozNY/DPgyaZ6gAwAJ
    from .calendar import get_dates, get_week, get_first_week, subset_dates
    from .routes import (
        get_routes,
        compute_route_stats,
        build_zero_route_time_series,
        compute_route_time_series,
        build_route_timetable,
        geometrize_routes,
        routes_to_geojson,
        map_routes,
    )
    from .shapes import (
        append_dist_to_shapes,
        geometrize_shapes,
        build_geometry_by_shape,
        shapes_to_geojson,
        get_shapes_intersecting_geometry,
    )
    from .stops import (
        get_stops,
        compute_stop_activity,
        compute_stop_stats,
        build_zero_stop_time_series,
        compute_stop_time_series,
        build_stop_timetable,
        geometrize_stops,
        build_geometry_by_stop,
        stops_to_geojson,
        get_stops_in_polygon,
        map_stops,
    )
    from .stop_times import (
        get_stop_times,
        append_dist_to_stop_times,
        get_start_and_end_times,
    )
    from .trips import (
        is_active_trip,
        get_trips,
        compute_trip_activity,
        compute_busiest_date,
        compute_trip_stats,
        locate_trips,
        geometrize_trips,
        trips_to_geojson,
        map_trips,
    )
    from .miscellany import (
        summarize,
        describe,
        assess_quality,
        convert_dist,
        compute_feed_stats,
        compute_feed_time_series,
        create_shapes,
        compute_bounds,
        compute_center,
        restrict_to_dates,
        restrict_to_routes,
        restrict_to_polygon,
        compute_screen_line_counts,
    )
    from .validators import (
        validate,
        check_agency,
        check_calendar,
        check_calendar_dates,
        check_fare_attributes,
        check_fare_rules,
        check_feed_info,
        check_frequencies,
        check_routes,
        check_shapes,
        check_stops,
        check_stop_times,
        check_transfers,
        check_trips,
    )
    from .cleaners import (
        clean_ids,
        clean_times,
        clean_route_short_names,
        drop_zombies,
        aggregate_routes,
        aggregate_stops,
        clean,
        drop_invalid_columns,
    )

    def __init__(
        self,
        dist_units: str,
        agency: Optional[DataFrame] = None,
        stops: Optional[DataFrame] = None,
        routes: Optional[DataFrame] = None,
        trips: Optional[DataFrame] = None,
        stop_times: Optional[DataFrame] = None,
        calendar: Optional[DataFrame] = None,
        calendar_dates: Optional[DataFrame] = None,
        fare_attributes: Optional[DataFrame] = None,
        fare_rules: Optional[DataFrame] = None,
        shapes: Optional[DataFrame] = None,
        frequencies: Optional[DataFrame] = None,
        transfers: Optional[DataFrame] = None,
        feed_info: Optional[DataFrame] = None,
    ):
        """
        Assume that every non-None input is a DataFrame,
        except for ``dist_units`` which should be a string in
        :const:`.constants.DIST_UNITS`.

        No other format checking is performed.
        In particular, a Feed instance need not represent a valid GTFS
        feed.
        """
        # Set primary attributes from inputs.
        # The @property magic below will then
        # validate some and set some derived attributes
        for prop, val in locals().items():
            if prop in cs.FEED_ATTRS_1:
                setattr(self, prop, val)

    @property
    def dist_units(self):
        """
        The distance units of the Feed.
        """
        return self._dist_units

    @dist_units.setter
    def dist_units(self, val):
        if val not in cs.DIST_UNITS:
            raise ValueError(
                f"Distance units are required and " f"must lie in {cs.DIST_UNITS}"
            )
        else:
            self._dist_units = val

    @property
    def trips(self):
        """
        The trips table of this Feed.
        """
        return self._trips

    @trips.setter
    def trips(self, val):
        """
        Update ``self._trips_i`` if ``self.trips`` changes.
        """
        self._trips = val
        if val is not None and not val.empty:
            self._trips_i = self._trips.set_index("trip_id")
        else:
            self._trips_i = None

    @property
    def calendar(self):
        """
        The calendar table of this Feed.
        """
        return self._calendar

    @calendar.setter
    def calendar(self, val):
        """
        Update ``self._calendar_i``if ``self.calendar`` changes.
        """
        self._calendar = val
        if val is not None and not val.empty:
            self._calendar_i = self._calendar.set_index("service_id")
        else:
            self._calendar_i = None

    @property
    def calendar_dates(self):
        """
        The calendar_dates table of this Feed.
        """
        return self._calendar_dates

    @calendar_dates.setter
    def calendar_dates(self, val):
        """
        Update ``self._calendar_dates_g``
        if ``self.calendar_dates`` changes.
        """
        self._calendar_dates = val
        if val is not None and not val.empty:
            self._calendar_dates_g = self._calendar_dates.groupby(
                ["service_id", "date"]
            )
        else:
            self._calendar_dates_g = None

    def __str__(self):
        """
        Print the first five rows of each GTFS table.
        """
        d = {}
        for table in cs.GTFS_REF["table"].unique():
            try:
                d[table] = getattr(self, table).head(5)
            except:
                d[table] = None
        d["dist_units"] = self.dist_units

        return "\n".join([f"* {k} --------------------\n\t{v}" for k, v in d.items()])

    def __eq__(self, other):
        """
        Define two feeds be equal if and only if their
        :const:`.constants.FEED_ATTRS` attributes are equal,
        or almost equal in the case of DataFrames
        (but not groupby DataFrames).
        Almost equality is checked via :func:`.helpers.almost_equal`,
        which   canonically sorts DataFrame rows and columns.
        """
        # Return False if failures
        for key in cs.FEED_ATTRS_1:
            x = getattr(self, key)
            y = getattr(other, key)
            # DataFrame case
            if isinstance(x, pd.DataFrame):
                if not isinstance(y, pd.DataFrame) or not hp.almost_equal(x, y):
                    return False
            # Other case
            else:
                if x != y:
                    return False
        # No failures
        return True

    def copy(self) -> "Feed":
        """
        Return a copy of this feed, that is, a feed with all the same
        attributes.
        """
        other = Feed(dist_units=self.dist_units)
        for key in set(cs.FEED_ATTRS) - set(["dist_units"]):
            value = getattr(self, key)
            if isinstance(value, pd.DataFrame):
                # Pandas copy DataFrame
                value = value.copy()
            elif isinstance(value, pd.core.groupby.DataFrameGroupBy):
                # Pandas does not have a copy method for groupby objects
                # as far as i know
                value = deepcopy(value)
            setattr(other, key, value)

        return other

    def write(self, path: Path, ndigits: int = 6) -> None:
        """
        Write this Feed to the given path.
        If the path end in '.zip', then write the feed as a zip archive.
        Otherwise assume the path is a directory, and write the feed as a
        collection of CSV files to that directory, creating the directory
        if it does not exist.
        Round all decimals to ``ndigits`` decimal places.
        All distances will be the distance units ``feed.dist_units``.
        """
        path = Path(path)

        if path.suffix == ".zip":
            # Write to temporary directory before zipping
            zipped = True
            tmp_dir = tempfile.TemporaryDirectory()
            new_path = Path(tmp_dir.name)
        else:
            zipped = False
            if not path.exists():
                path.mkdir()
            new_path = path

        for table in cs.GTFS_REF["table"].unique():
            f = getattr(self, table)
            if f is None:
                continue

            f = f.copy()
            # Some columns need to be output as integers.
            # If there are NaNs in any such column,
            # then Pandas will format the column as float, which we don't want.
            f_int_cols = set(cs.INT_COLS) & set(f.columns)
            for s in f_int_cols:
                f[s] = f[s].fillna(-1).astype(int).astype(str).replace("-1", "")
            p = new_path / (table + ".txt")
            f.to_csv(str(p), index=False, float_format=f"%.{ndigits}f")

        # Zip directory
        if zipped:
            basename = str(path.parent / path.stem)
            shutil.make_archive(basename, format="zip", root_dir=tmp_dir.name)
            tmp_dir.cleanup()


# -------------------------------------
# Functions about input and output
# -------------------------------------
def list_feed(path: Path) -> DataFrame:
    """
    Given a path (string or Path object) to a GTFS zip file or
    directory, record the file names and file sizes of the contents,
    and return the result in a DataFrame with the columns:

    - ``'file_name'``
    - ``'file_size'``
    """
    path = Path(path)
    if not path.exists():
        raise ValueError(f"Path {path} does not exist")

    # Collect rows of DataFrame
    rows = []
    if path.is_file():
        # Zip file
        with zipfile.ZipFile(str(path)) as src:
            for x in src.infolist():
                if x.filename == "./":
                    continue
                d = {}
                d["file_name"] = x.filename
                d["file_size"] = x.file_size
                rows.append(d)
    else:
        # Directory
        for x in path.iterdir():
            d = {}
            d["file_name"] = x.name
            d["file_size"] = x.stat().st_size
            rows.append(d)

    return pd.DataFrame(rows)


def _read_feed_from_path(path: Path, dist_units: str) -> "Feed":
    """
    Helper function for :func:`read_feed`.
    Create a Feed instance from the given path and given distance units.
    The path should be a directory containing GTFS text files or a
    zip file that unzips as a collection of GTFS text files
    (and not as a directory containing GTFS text files).
    The distance units given must lie in :const:`constants.dist_units`

    Notes:

    - Ignore non-GTFS files in the feed
    - Automatically strip whitespace from the column names in GTFS files

    """
    path = Path(path)
    if not path.exists():
        raise ValueError(f"Path {path} does not exist")

    # Unzip path to temporary directory if necessary
    if path.is_file():
        zipped = True
        tmp_dir = tempfile.TemporaryDirectory()
        src_path = Path(tmp_dir.name)
        shutil.unpack_archive(str(path), tmp_dir.name, "zip")
    else:
        zipped = False
        src_path = path

    # Read files into feed dictionary of DataFrames
    feed_dict = {table: None for table in cs.GTFS_REF["table"]}
    for p in src_path.iterdir():
        table = p.stem
        # Skip empty files, irrelevant files, and files with no data
        if (
            p.is_file()
            and p.stat().st_size
            and p.suffix == ".txt"
            and table in feed_dict
        ):
            # utf-8-sig gets rid of the byte order mark (BOM);
            # see http://stackoverflow.com/questions/17912307/u-ufeff-in-python-string
            df = pd.read_csv(p, dtype=cs.DTYPE, encoding="utf-8-sig")
            if not df.empty:
                feed_dict[table] = cn.clean_column_names(df)

    feed_dict["dist_units"] = dist_units

    # Delete temporary directory
    if zipped:
        tmp_dir.cleanup()

    # Create feed
    return Feed(**feed_dict)


def _read_feed_from_url(url: str, dist_units: str) -> "Feed":
    """
    Helper function for :func:`read_feed`.
    Create a Feed instance from the given URL and given distance units.
    Assume the URL is valid and let the Requests library raise any errors.

    Notes:

    - Ignore non-GTFS files in the feed
    - Automatically strip whitespace from the column names in GTFS files


    """
    r = requests.get(url)
    with tempfile.NamedTemporaryFile() as f:
        f.write(r._content)
        f.seek(0)
        return _read_feed_from_path(f.name, dist_units=dist_units)


def read_feed(path_or_url: Union[Path, str], dist_units: str) -> "Feed":
    """
    Create a Feed instance from the given path or URL and given distance units.
    If the path exists, then call :func:`_read_feed_from_path`.
    Else if the URL has OK status according to Requests, then call
    :func:`_read_feed_from_url`.
    Else raise a ValueError.

    Notes:

    - Ignore non-GTFS files in the feed
    - Automatically strip whitespace from the column names in GTFS files

    """
    if Path(path_or_url).exists():
        return _read_feed_from_path(path_or_url, dist_units=dist_units)
    elif requests.head(path_or_url).ok:
        return _read_feed_from_url(path_or_url, dist_units=dist_units)
    else:
        raise ValueError("Path does not exist or URL has bad status.")
