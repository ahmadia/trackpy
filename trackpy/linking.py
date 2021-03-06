from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import six
from six.moves import zip, range
from copy import copy
import itertools, functools
from collections import deque, Iterable

import numpy as np
from scipy.spatial import cKDTree
import pandas as pd

from .utils import print_update
from .try_numba import try_numba_autojit, NUMBA_AVAILABLE

class TreeFinder(object):

    def __init__(self, points):
        """Takes a list of particles.
        """
        self.points = copy(points)
        self.rebuild()

    def add_point(self, pt):
        self.points.append(pt)
        self._clean = False

    def rebuild(self, coord_map=None):
        """Rebuilds tree from ``points`` attribute.

        coord_map : function (optional)
            Called with a list of N Point instances, returns their "effective" locations,
            as an N x d array (or list of tuples). Used for prediction (see "predict" module).

        rebuild() needs to be called after ``add_point()`` and before tree is used for
        spatial queries again (i.e. when memory is turned on).
        """

        if coord_map is None:
            coord_map = functools.partial(map, lambda x: x.pos)
        coords = np.asarray(list(coord_map(self.points)))
        if len(self.points) == 0:
            raise ValueError('Frame (aka level) contains zero points')
        self._kdtree = cKDTree(coords, 15)
        # This could be tuned
        self._clean = True

    @property
    def kdtree(self):
        if not self._clean:
            self.rebuild()
        return self._kdtree


class HashTable(object):
    '''
    :param dims: the range of the data to be put in the hash table.  0<data[k]<dims[k]
    :param box_size: how big each box should be in data units.  The same scale is used for all dimensions

    Basic hash table to fast look up of particles in the region of a given particle
    '''
    class Out_of_hash_excpt(Exception):
        """
        :py:exc:`Exception` for indicating that a particle is outside of the
        valid range for this hash table."""
        pass

    def __init__(self, dims, box_size):
        '''
        Sets up the hash table

        '''
        self.dims = dims                  # the dimensions of the data
        self.box_size = box_size          # the size of boxes to use in the units of the data
        self.hash_dims = np.ceil(np.array(dims) / box_size)

        self.hash_table = [[] for j in range(int(np.prod(self.hash_dims)))]
        self.spat_dims = len(dims)        # how many spatial dimensions
        self.cached_shifts = None
        self.cached_rrange = None
        self.strides = np.cumprod(np.concatenate(([1], self.hash_dims[1:])))[::-1]

    def get_region(self, point, rrange):
        '''
        :param point: point to find the features around
        :param rrange: the size of the ball to search in


        Returns all the particles with in the region of maximum radius
        rrange in data units


        can raise :py:exc:`Out_of_hash_excpt`
        '''
        hash_size = self.hash_dims
        center = np.floor(point.pos / self.box_size)
        if any(center >= hash_size) or any(center < 0):
            raise Hash_table.Out_of_hash_excpt("cord out of range")

        rrange = int(np.ceil(rrange / self.box_size))

        # check if we have already computed the shifts
        if rrange == self.cached_rrange and self.cached_shifts is not None:
            shifts = self.cached_shifts   # if we have, use them
        # Other wise, generate them
        else:
            if self.spat_dims == 2:
                shifts = [np.array([j, k])
                          for j in range(-rrange, rrange + 1)
                          for k in range(-rrange, rrange + 1)]
            elif self.spat_dims == 3:
                shifts = [np.array([j, k, m])
                          for j in range(-rrange, rrange + 1)
                          for k in range(-rrange, rrange + 1)
                          for m in range(-rrange, rrange + 1)]
            else:
                raise NotImplementedError('only 2 and 3 dimensions implemented')
            self.cached_rrange = rrange   # and save them
            self.cached_shifts = shifts
        region = []

        for s in shifts:

            cord = center + s
            if any(cord >= hash_size) or any(cord < 0):
                continue
            indx = int(sum(cord * self.strides))
            region.extend(self.hash_table[indx])
        return region

    def add_point(self, point):
        """
        :param point: object representing the feature to add to the hash table

        Adds the `point` to the hash table.  Assumes that :py:attr:`point.pos` exists and
        is the array-like.




        can raise :py:exc:`~Hash_table.Out_of_hash_excpt`

        """
        cord = np.floor(np.asarray(point.pos) / self.box_size)
        hash_size = self.hash_dims
        if any(cord >= hash_size) or any(cord < 0):
            raise Hash_table.Out_of_hash_excpt("cord out of range")
        indx = int(sum(cord * self.strides))
        self.hash_table[indx].append(point)


class Track(object):
    '''
    :param point: The first feature in the track if not  `None`.
    :type point: :py:class:`~trackpy.tracking.Point`

    Base class for objects to represent linked tracks.  Includes logic
    for adding, removing features to the track.  This can be sub-classed
    to provide additional track level computation as needed.


    '''
    count = 0

    def __init__(self, point=None):
        self.points = []
        # will take initiator point
        if not point is None:
            self.add_point(point)

        self.indx = Track.count           # unique id
        Track.count += 1

    def __iter__(self):
        return self.points.__iter__()

    def __len__(self):
        return len(self.points)

    def __eq__(self, other):
        return self.index == other.index

    def __neq__(self, other):
        return not self.__eq__(other)
    __hash__ = None

    def add_point(self, point):
        '''
        :param point: point to add
        :type point:  :py:class:`~trackpy.tracking.Point`

        Appends the point to this track. '''
        self.points.append(point)
        point.add_to_track(self)

    def remove_point(self, point):
        '''
        :param point: point to remove from this track
        :type point:  :py:class:`~trackpy.tracking.Point`

        removes a point from this track'''
        self.points.remove(point)
        point._track = None

    def last_point(self):
        '''
        :rtype: :py:class:`~trackpy.tracking.Point`

        Returns the last point on the track'''
        return self.points[-1]

    def __repr__(self):
        return "<%s %d>" % (self.__class__.__name__, self.indx)

    @classmethod
    def reset_counter(cls, c=0):
        cls.count = 0


class DummyTrack(object):
    "Does not store points, thereby conserving memory."

    track_id = itertools.count(0)

    def __init__(self, point):
       self.id = next(DummyTrack.track_id)
       self.indx = self.id  # redundant, but like trackpy
       if point is not None:
           self.add_point(point)

    def add_point(self, point):
        point.add_to_track(self)

    @classmethod
    def reset_counter(cls, c=0):
        cls.track_id = itertools.count(c)


class Point(object):
    '''
    Base class for point (features) used in tracking.  This class
    contains all of the general stuff for interacting with
    :py:class:`~trackpy.tracking.Track` objects.



    .. note:: To be used for tracking this class must be sub-classed to provide a :py:func:`distance` function.  Child classes **MUST** call :py:func:`Point.__init__`.  (See :py:class:`~trackpy.tracking.PointND` for example. )
    '''
    count = 0

    def __init__(self):
        self._track = None
        self.uuid = Point.count         # unique id for __hash__
        Point.count += 1

    ## def __eq__(self, other):
    ##     return self.uuid == other.uuid

    ## def __neq__(self, other):
    ##     return not self.__eq__(other)

    def add_to_track(self, track):
        '''
        :param track: the track to assign to this :py:class:`Point`

        Sets the track of a :py:class:`Point` object.  Raises
        :py:exc:`Exception` if the object is already assigned a track.



        '''
        if self._track is not None:
            raise Exception("trying to add a particle already in a track")
        self._track = track

    def remove_from_track(self, track):
        '''
        :param track: the track to disassociate from this :py:class:`Point`

        Removes this point from the given track. Raises :py:exc:`Exception` if
        particle not associated with the given track.


        '''
        if self._track != track:
            raise Exception("Point not associated with given track")
        track.remove_point(self)

    def in_track(self):
        '''
        :rtype: bool

        Returns if a point is associated with a track '''
        return self._track is not None

    @property
    def track(self):
        """Returns the track that this :class:`Point` is in.  May be `None` """
        return self._track


class PointND(Point):
    '''
    :param t: a time-like variable.
    :param pos: position of feature
    :type pos: iterable of length d

    Version of :py:class:`Point` for tracking in flat space with
    non-periodic boundary conditions.
    '''

    def __init__(self, t, pos):
        Point.__init__(self)                  # initialize base class
        self.t = t                            # time
        self.pos = np.asarray(pos)            # position in ND space

    def distance(self, other_point):
        '''
        :param other_point: point to get distance to.
        :type other_point: :py:class:`~trackpy.tracking.Point`

        Returns the absolute distance between this point and other_point

        '''
        return np.sqrt(np.sum((self.pos - other_point.pos) ** 2))

    def __str__(self):
        return "({t}, {p})".format(t=self.t, p=self.pos)

    def __repr__(self):
        coords = '(' + (', '.join(["{:.3f}"]*len(self.pos))).format(*self.pos) + ')'
        track = " in Track %d" % self.track.indx if self.track else ""
        return "<%s at %d, " % (self.__class__.__name__, self.t) + coords + track + ">"

class IndexedPointND(PointND):

    def __init__(self, t, pos, id):
        PointND.__init__(self, t, pos)  # initialize base class
        self.id = id  # unique ID derived from sequential index


def link(levels, search_range, hash_generator, memory=0, track_cls=None,
         neighbor_strategy='BTree', link_strategy='recursive'):
    """Link features into trajectories, assigning a label to each trajectory.

    This function is deprecated and lacks some recently-added options,
    thought it is still accurate. Use link_df or link_iter.

    Parameters
    ----------
    levels : iterable of iterables containing Points objects
        e.g., a list containing lists with the Points in each frame
    search_range : integer
        the maximum distance features can move between frames
    hash_generator : a function that returns a HashTable
        only used if neighbor_strategy is set to 'BTree' (default)
    memory : integer
        the maximum number of frames during which a feature can vanish,
        then reppear nearby, and be considered the same particle. 0 by default.
    neighbor_strategy : {'BTree', 'KDTree'}
        algorithm used to identify nearby features
    link_strategy : {'recursive', 'nonrecursive', 'numba', 'auto'}
        algorithm used to resolve subnetworks of nearby particles
        'auto' uses numba if available

    Returns
    -------
    tracks : list of Track (or track_cls) objects

    See Also
    --------
    link_df, link_iter
    """
    # An informative error to help newbies who go astray
    if isinstance(levels, pd.DataFrame):
        raise TypeError("Instead of link, use link_df, which accepts "
                        "pandas DataFrames.")

    if track_cls is None:
        track_cls = Track  # stores Points
    label_generator = link_iter(iter(levels), search_range, memory=memory,
                                neighbor_strategy=neighbor_strategy,
                                link_strategy=link_strategy,
                                track_cls=track_cls,
                                hash_generator=hash_generator)
    labels = list(label_generator)
    points = [level for level_list in levels for level in level_list]  # flat
    points = pd.Series(points)
    labels = [label.track.indx for label_list in labels
              for label in label_list]  # flat
    grouped = points.groupby(labels)
    representative_points = grouped.first()  # one point from each Track
    tracks = representative_points.apply(lambda x: x.track)
    return tracks

def link_df(features, search_range, memory=0,
            neighbor_strategy='KDTree', link_strategy='auto',
            predictor=None, hash_size=None, box_size=None,
            pos_columns=None, t_column=None, verify_integrity=False,
            retain_index=False):
    """Link features into trajectories, assigning a label to each trajectory.

    Parameters
    ----------
    features : DataFrame
        Must include any number of column(s) for position and a column of
        frame numbers. By default, 'x' and 'y' are expected for position,
        and 'frame' is expected for frame number. See below for options to use
        custom column names.
    search_range : integer
        the maximum distance features can move between frames
    memory : integer
        the maximum number of frames during which a feature can vanish,
        then reppear nearby, and be considered the same particle. 0 by default.
    neighbor_strategy : {'KDTree', 'BTree'}
        algorithm used to identify nearby features
    link_strategy : {'recursive', 'nonrecursive', 'numba', 'auto'}
        algorithm used to resolve subnetworks of nearby particles
        'auto' uses numba if available
    predictor : function (optional)
        Improve performance by guessing where a particle will be in the next frame.
        For examples of how this works, see the "predict" module.

    Returns
    -------
    trajectories : DataFrame
        This is the input features DataFrame, now with a new column labeling
        each particle with an ID number. This is not a copy.

    Other Parameters
    ----------------
    pos_columns : DataFrame column names (unlimited dimensions)
        Default is ['x', 'y']
    t_column : DataFrame column name
        Default is 'frame'
    hash_size : sequence
        For 'BTree' mode only. Define the shape of the search region.
        If None (default), infer shape from range of data.
    box_size : sequence
        For 'BTree' mode only. Define the parition size to optimize
        performance. If None (default), the search_range is used, which is
        a reasonable guess for best performance.
    verify_integrity : boolean
        False by default for fastest performance.
        Use True if you suspect a bug in linking.
    retain_index : boolean
        By default, the index is reset to be sequential. To keep the original
        index, set to True. Default is fine unless you devise a special use.
    """
    # Assign defaults. (Do it here to avoid "mutable defaults" issue.)
    if pos_columns is None:
        pos_columns = ['x', 'y']
    if t_column is None:
        t_column = 'frame'
    if hash_size is None:
        MARGIN = 1 # avoid OutOfHashException
        hash_size = features[pos_columns].max() + MARGIN

    # Group the DataFrame by time steps and make a 'level' out of each
    # one, using the index to keep track of Points.
    if retain_index:
        orig_index = features.index.copy()  # Save it; restore it at the end.
    features.reset_index(inplace=True, drop=True)
    levels = (_build_level(frame, pos_columns, t_column) for frame_no, frame \
              in features.groupby(t_column))
    labeled_levels = link_iter(
        levels, search_range, memory=memory, predictor=predictor,
        neighbor_strategy=neighbor_strategy, link_strategy=link_strategy,
        hash_size=hash_size, box_size=box_size)

    # Do the tracking, and update the DataFrame after each iteration.
    features['particle'] = np.nan  # placeholder
    for level in labeled_levels:
        index = [x.id for x in level]
        labels = pd.Series([x.track.id for x in level], index)
        frame_no = next(iter(level)).t  # uses an arbitary element from the set
        if verify_integrity:
            # This checks that the labeling is sane and tries
            # to raise informatively if some unknown bug in linking
            # produces a malformed labeling.
            _verify_integrity(frame_no, labels)
            # an additional check particular to link_df
            if len(labels) > len(features[features[t_column] == frame_no]):
                raise UnknownLinkingError("There are more labels than "
                                          "particles to be labeled in Frame "
                                           "%d".format(frame_no))
        features['particle'].update(labels)

        msg = "Frame %d: %d trajectories present" % (frame_no, len(labels))
        print_update(msg)

    if retain_index:
        features.index = orig_index
        # And don't bother to sort -- user must be doing something special.
    else:
        features.sort(['particle', t_column], inplace=True)
        features.reset_index(drop=True, inplace=True)
    return features

def link_df_iter(features, search_range, memory=0,
            neighbor_strategy='KDTree', link_strategy='auto',
            hash_size=None, box_size=None, predictor=None,
            pos_columns=None, t_column=None, verify_integrity=True,
            retain_index=False):
    """Link features into trajectories, assigning a label to each trajectory.

    Parameters
    ----------
    features : iterable of DataFrames
        Each DataFrame must include any number of column(s) for position and a
        column of frame numbers. By default, 'x' and 'y' are expected for position,
        and 'frame' is expected for frame number. See below for options to use
        custom column names.
    search_range : integer
        the maximum distance features can move between frames
    memory : integer
        the maximum number of frames during which a feature can vanish,
        then reppear nearby, and be considered the same particle. 0 by default.
    neighbor_strategy : {'KDTree', 'BTree'}
        algorithm used to identify nearby features. Note that when using
        BTree, you must specify hash_size
    link_strategy : {'recursive', 'nonrecursive', 'numba', 'auto'}
        algorithm used to resolve subnetworks of nearby particles
        'auto' uses numba if available
    predictor : function (optional)
        Improve performance by guessing where a particle will be in the next frame.
        For examples of how this works, see the "predict" module.

    Returns
    -------
    trajectories : DataFrame
        This is the input features DataFrame, now with a new column labeling
        each particle with an ID number. This is not a copy.

    Other Parameters
    ----------------
    pos_columns : DataFrame column names (unlimited dimensions)
        Default is ['x', 'y']
    t_column : DataFrame column name
        Default is 'frame'
    hash_size : sequence
        For 'BTree' mode only. Define the shape of the search region.
    box_size : sequence
        For 'BTree' mode only. Define the parition size to optimize
        performance. If None (default), the search_range is used, which is
        a reasonable guess for best performance.
    verify_integrity : boolean
        False by default, for fastest performance.
        Use True if you suspect a bug in linking.
    retain_index : boolean
        By default, the index is reset to be sequential. To keep the original
        index, set to True. Default is fine unless you devise a special use.
    """
    # Assign defaults. (Do it here to avoid "mutable defaults" issue.)
    if pos_columns is None:
        pos_columns = ['x', 'y']
    if t_column is None:
        t_column = 'frame'

    # Group the DataFrame by time steps and make a 'level' out of each
    # one, using the index to keep track of Points.

    feature_iter = iter(features)
    # To allow retain_index
    features_for_reset, features_forindex = itertools.tee(feature_iter)
    index_iter = (fr.index.copy() for fr in features_forindex)
    # To allow extra columns to be recovered later
    features_forlinking, features_forpost = itertools.tee(
        (frame.reset_index(drop=True) for frame in features_for_reset))
    levels = (_build_level(frame, pos_columns, t_column) \
                                                for frame in features_forlinking)
    labeled_levels = link_iter(
        levels, search_range, memory=memory, predictor=predictor,
        neighbor_strategy=neighbor_strategy, link_strategy=link_strategy,
        hash_size=hash_size, box_size=box_size)

    # Re-assemble the features data, now with track labels and (if desired)
    # the original index.
    for labeled_level, source_features, old_index in zip(
            labeled_levels, features_forpost, index_iter):
        features = source_features.copy()
        features['particle'] = np.nan  # placeholder
        index = [x.id for x in  labeled_level]
        labels = pd.Series([x.track.id for x in labeled_level], index)
        frame_no = next(iter(labeled_level)).t  # uses an arbitary element from the set
        if verify_integrity:
            # This checks that the labeling is sane and tries
            # to raise informatively if some unknown bug in linking
            # produces a malformed labeling.
            _verify_integrity(frame_no, labels)
            # additional checks particular to link_df_iter
            if not all(frame_no == source_features.frame.values):
                raise UnknownLinkingError("The features passed for Frame %d "
                                          "do not all share the same frame "
                                          "number.".format(frame_no))
            if len(labels) > len(features):
                raise UnknownLinkingError("There are more labels than "
                                          "particles to be labeled in Frame "
                                           "%d".format(frame_no))
        features['particle'].update(labels)

        if retain_index:
            features.index = old_index
        else:
            features.sort('particle', inplace=True)
            features.reset_index(drop=True, inplace=True)

        msg = "Frame %d: %d trajectories present" % (frame_no, len(labels))
        print_update(msg)

        yield features

def _build_level(frame, pos_columns, t_column):
    "Return IndexPointND objects for a DataFrame of points."
    build_pt = lambda x: IndexedPointND(x[1], x[0][1].values, x[0][0])
    # iterrows() returns: (index which we use as feature id, data)
    zipped = zip(frame[pos_columns].iterrows(), frame[t_column])
    return [build_pt(pt) for pt in zipped]

class UnknownLinkingError(Exception):
    pass


def _verify_integrity(frame_no, labels):
    if labels.duplicated().sum() > 0:
        raise UnknownLinkingError(
            "There are two particles with the same label in Frame %d.".format(
                frame_no))
    if np.any(labels < 0):
        raise UnknownLinkingError("Some particles were not labeled "
                                  "in Frame %d.".format(frame_no))

def link_iter(levels, search_range, memory=0,
              neighbor_strategy='KDTree', link_strategy='auto',
              hash_size=None, box_size=None, predictor=None,
              track_cls=None, hash_generator=None):
    """Link features into trajectories, assigning a label to each trajectory.

    Parameters
    ----------
    levels : iterable of iterables containing Points objects
        e.g., a list containing lists with the Points in each frame
    search_range : integer
        the maximum distance features can move between frames
    memory : integer
        the maximum number of frames during which a feature can vanish,
        then reppear nearby, and be considered the same particle. 0 by default.
    neighbor_strategy : {'KDTree', 'BTree'}
        algorithm used to identify nearby features
    link_strategy : {'recursive', 'nonrecursive', 'numba', 'auto'}
        algorithm used to resolve subnetworks of nearby particles
        'auto' uses numba if available
    predictor : function (optional)
        Improve performance by guessing where a particle will be in the next frame.
        For examples of how this works, see the "predict" module.

    Yields
    ------
    labels : list of integers
       labeling the features in the given level

    Other Parameters
    ----------------
    hash_size : sequence
        For 'BTree' mode only. Define the shape of the search region.
        (Higher-level wrappers of link infer this from the data.)
    box_size : sequence
        For 'BTree' mode only. Define the parition size to optimize
        performance. If None (default), the search_range is used, which is
        a reasonable guess for best performance.
    track_cls : class (optional)
        for special uses, you can specify a custom class that holds
        each Track
    hash_generator : function (optional)
        a function that returns a HashTable, included for legacy support.
        Specifying hash_size and box_size (above) fully defined a HashTable.
    """
    if hash_generator is None:
        if neighbor_strategy == 'BTree':
            if hash_size is None:
                raise ValueError("In 'BTree' mode, you must specify hash_size")
            if box_size is None:
                box_size = search_range
        hash_generator = lambda: Hash_table(hash_size, box_size)
    if track_cls is None:
        track_cls = DummyTrack  # does not store Points

    linkers = {'recursive': recursive_linker_obj,
               'nonrecursive': nonrecursive_link}
    if NUMBA_AVAILABLE:
        linkers['numba'] = numba_link
        linkers['auto'] = linkers['numba']
    else:
        linkers['auto'] = linkers['recursive']
    try:
        subnet_linker = linkers[link_strategy]
    except KeyError:
        raise ValueError("link_strategy must be one of: " + ', '.join(linkers.keys()))

    if neighbor_strategy not in ['KDTree', 'BTree']:
        raise ValueError("neighbor_strategy must be 'KDTree' or 'BTree'")

    level_iter = iter(levels)
    prev_level = next(level_iter)
    prev_set = set(prev_level)

    # Make a Hash / Tree for the first level.
    if neighbor_strategy == 'BTree':
        prev_hash = hash_generator()
        for p in prev_set:
            prev_hash.add_point(p)
    elif neighbor_strategy == 'KDTree':
        prev_hash = TreeFinder(prev_level)

    for p in prev_set:
        p.forward_cands = []

    try:
        # Start ID numbers from zero, incompatible with multithreading.
        track_cls.reset_counter()
    except AttributeError:
        # must be using a custom Track class without this method
        pass


    # Assume everything in first level starts a Track.
    track_lst = [track_cls(p) for p in prev_set]
    mem_set = set()

    # Initialize memory with empty sets.
    mem_history = []
    for j in range(memory):
        mem_history.append(set())

    yield list(prev_set)  # Short-circuit the loop on first call.

    for cur_level in levels:
        # Create the set for the destination level.
        cur_set = set(cur_level)
        tmp_set = set(cur_level)  # copy used in next loop iteration

        # First, a bit of unfinished business:
        # If prediction is enabled, we need to update the positions in prev_hash
        # to where we think they'll be in the frame corresponding to cur_level.
        if predictor is not None:
            # This only works for KDTree right now, because KDTree can store particle
            # positions in a separate data structure from the PointND instances.
            if not isinstance(prev_hash, TreeFinder):
                raise NotImplementedError(
                    'Prediction works with the "KDTree" neighbor_strategy only.')
            # Get the time of cur_level from its first particle
            t_next = list(itertools.islice(cur_level, 0, 1))[0].t
            targeted_predictor = functools.partial(predictor, t_next)
            prev_hash.rebuild(coord_map=targeted_predictor) # Rewrite positions

        # Now we can process the new particles.
        # Make a Hash / Tree for the destination level.
        if neighbor_strategy == 'BTree':
            cur_hash = hash_generator()
            for p in cur_set:
                cur_hash.add_point(p)
        elif neighbor_strategy == 'KDTree':
            cur_hash = TreeFinder(cur_level)

        # Set up attributes for keeping track of possible connections.
        for p in cur_set:
            p.back_cands = []
            p.forward_cands = []

        # Sort out what can go to what.
        assign_candidates(cur_level, prev_hash, search_range,
                          neighbor_strategy)

        # sort the candidate lists by distance
        for p in cur_set:
            p.back_cands.sort(key=lambda x: x[1])
        for p in prev_set:
            p.forward_cands.sort(key=lambda x: x[1])

        new_mem_set = set()
        # while there are particles left to link, linkge the repo
        while len(cur_set) > 0:
            p = cur_set.pop()
            bc_c = len(p.back_cands)
            # no backwards candidates
            if bc_c == 0:
                # add a new track
                track_lst.append(track_cls(p))
                # clean up tracking apparatus
                del p.back_cands
                # short circuit loop
                continue
            if bc_c == 1:
                # one backwards candidate
                b_c_p = p.back_cands[0]
                # and only one forward candidatege the repo
                b_c_p_0 = b_c_p[0]
                if len(b_c_p_0.forward_cands) == 1:
                    # add to the track of the candidate
                    b_c_p_0.track.add_point(p)
                    _maybe_remove(mem_set, b_c_p_0)
                    # clean up tracking apparatus
                    del p.back_cands
                    del b_c_p_0.forward_cands
                    prev_set.discard(b_c_p_0)
                    # short circuit loop
                    continue
            # we need to generate the sub networks
            done_flg = False
            s_sn = set()                  # source sub net
            d_sn = set()                  # destination sub net
            # add working particle to destination sub-net
            d_sn.add(p)
            while not done_flg:
                d_sn_sz = len(d_sn)
                s_sn_sz = len(s_sn)
                for dp in d_sn:
                    for c_sp in dp.back_cands:
                        s_sn.add(c_sp[0])
                        prev_set.discard(c_sp[0])
                for sp in s_sn:
                    for c_dp in sp.forward_cands:
                        d_sn.add(c_dp[0])
                        cur_set.discard(c_dp[0])
                done_flg = (len(d_sn) == d_sn_sz) and (len(s_sn) == s_sn_sz)

            # add in penalty for not linking
            for _s in s_sn:
                _s.forward_cands.append((None, search_range))

            spl, dpl = subnet_linker(s_sn, len(d_sn), search_range)

            # Identify the particles in the destination set that
            # were not linked to.
            d_remain = set(d for d in d_sn if d is not None)  # TODO DAN
            d_remain -= set(d for d in dpl if d is not None)
            for dp in d_remain:
                # if unclaimed destination particle, a track is born!
                track_lst.append(track_cls(dp))
                # clean up
                del dp.back_cands

            for sp, dp in zip(spl, dpl):
                # do linking and clean up
                if sp is not None and dp is not None:
                    sp.track.add_point(dp)
                    _maybe_remove(mem_set, sp)
                if dp is not None:  # TODO DAN 'Should never happen' - Nathan
                    del dp.back_cands
                if sp is not None:
                    del sp.forward_cands
                    if dp is None:
                        # add the unmatched source particles to the new
                        # memory set
                        new_mem_set.add(sp)

        # Remember the source particles left unlinked that were not in
        # a subnetwork.
        for sp in prev_set:
            new_mem_set.add(sp)

        # set prev_hash to cur hash
        prev_hash = cur_hash

        # add in the memory points
        # store the current level for use in next loop
        if memory > 0:
            # identify the new memory points
            new_mem_set -= mem_set
            mem_history.append(new_mem_set)
            # remove points that are now too old
            mem_set -= mem_history.pop(0)
            # add the new points
            mem_set |= new_mem_set
            # add the memory particles to what will be the next source
            # set
            tmp_set |= mem_set
            # add memory points to prev_hash (to be used as the next source)
            for m in mem_set:
                # add points to the hash
                prev_hash.add_point(m)
                # re-create the forward_cands list
                m.forward_cands = []

        prev_set = tmp_set

        yield cur_level


def assign_candidates(cur_level, prev_hash, search_range, neighbor_strategy):
    if neighbor_strategy == 'BTree':
        # (Tom's code)
        for p in cur_level:
            # get
            work_box = prev_hash.get_region(p, search_range)
            for wp in work_box:
                # this should get changed to deal with squared values
                # to save an eventually square root
                d = p.distance(wp)
                if d < search_range:
                    p.back_cands.append((wp, d))
                    wp.forward_cands.append((p, d))
    elif neighbor_strategy == 'KDTree':
        hashpts = prev_hash.points
        cur_coords = np.array([x.pos for x in cur_level])
        dists, inds = prev_hash.kdtree.query(cur_coords, 10, distance_upper_bound=search_range)
        nn = np.sum(np.isfinite(dists), 1) # Number of neighbors of each particle
        for i, p in enumerate(cur_level):
            for j in range(nn[i]):
                wp = hashpts[inds[i,j]]
                p.back_cands.append((wp, dists[i,j]))
                wp.forward_cands.append((p, dists[i,j]))


class SubnetOversizeException(Exception):
    '''An :py:exc:`Exception` to be raised when the sub-nets are too
    big to be efficiently linked.  If you get this then either reduce your search range
    or increase :py:attr:`sub_net_linker.MAX_SUB_NET_SIZE`'''
    pass


def recursive_linker_obj(s_sn, dest_size, search_range):
    snl = sub_net_linker(s_sn, dest_size, search_range)
    return zip(*snl.best_pairs)


class SubnetLinker(object):
    '''A helper class for implementing the Crocker-Grier tracking
    algorithm.  This class handles the recursion code for the sub-net linking'''
    MAX_SUB_NET_SIZE = 50

    def __init__(self, s_sn, dest_size, search_range):
        #        print 'made sub linker'
        self.s_sn = s_sn
        self.search_range = search_range
        self.s_lst = [s for s in s_sn]
        self.s_lst.sort(key=lambda x: len(x.forward_cands))
        self.MAX = len(self.s_lst)

        self.max_links = min(self.MAX, dest_size)
        self.best_pairs = None
        self.cur_pairs = deque([])
        self.best_sum = np.Inf
        self.d_taken = set()
        self.cur_sum = 0

        if self.MAX > sub_net_linker.MAX_SUB_NET_SIZE:
            raise SubnetOversizeException("Subnetwork contains %d points"
                                          % self.MAX)
        # do the computation
        self.do_recur(0)

    def do_recur(self, j):
        cur_s = self.s_lst[j]
        for cur_d, dist in cur_s.forward_cands:
            tmp_sum = self.cur_sum + dist
            if tmp_sum > self.best_sum:
                # if we are already greater than the best sum, bail we
                # can bail all the way out of this branch because all
                # the other possible connections (including the null
                # connection) are more expensive than the current
                # connection, thus we can discard with out testing all
                # leaves down this branch
                return
            if cur_d is not None and cur_d in self.d_taken:
                # we have already used this destination point, bail
                continue
            # add this pair to the running list
            self.cur_pairs.append((cur_s, cur_d))
            # add the destination point to the exclusion list
            if cur_d is not None:
                self.d_taken.add(cur_d)
            # update the current sum
            self.cur_sum = tmp_sum
            # buried base case
            # if we have hit the end of s_lst and made it this far, it
            # must be a better linking so save it.
            if j + 1 == self.MAX:
                tmp_sum = self.cur_sum + self.search_range * (self.max_links - len(self.d_taken))
                if tmp_sum < self.best_sum:
                    self.best_sum = tmp_sum
                    self.best_pairs = list(self.cur_pairs)
            else:
                # re curse!
                self.do_recur(j + 1)
            # remove this step from the working
            self.cur_sum -= dist
            if cur_d is not None:
                self.d_taken.remove(cur_d)
            self.cur_pairs.pop()
        pass


def nonrecursive_link(source_list, dest_size, search_range):

    #    print 'non-recursive', len(source_list), dest_size
    source_list = list(source_list)
    source_list.sort(key=lambda x: len(x.forward_cands))
    MAX = len(source_list)

    max_links = min(MAX, dest_size)
    k_stack = deque([0])
    j = 0
    cur_back = deque([])
    cur_sum_stack = deque([0])

    best_sum = np.inf

    best_back = None
    cand_list_list = [c.forward_cands for c in source_list]
    cand_lens = [len(c) for c in cand_list_list]

    while j >= 0:
        # grab everything from the end of the stack
        cur_sum = cur_sum_stack[-1]
        if j >= MAX:
            # base case, no more source candidates,
            # save the current configuration if it's better than the current max
            # add penalty for not linking to particles in the destination set
            tmp_sum = cur_sum + search_range * (max_links - len([d for d in cur_back if d is not None]))
            if tmp_sum < best_sum:
                best_sum = cur_sum
                best_back = list(cur_back)

            j -= 1
            k_stack.pop()
            cur_sum_stack.pop()
            cur_back.pop()

            # print 'we have a winner'
            # print '-------------------------'
            continue

        # see if we have any forward candidates
        k = k_stack[-1]
        if k >= cand_lens[j]:
            # no more candidates to try, this branch is done
            j -= 1
            k_stack.pop()
            cur_sum_stack.pop()
            if j >= 0:
                cur_back.pop()
            # print 'out of cands'
            # print '-------------------------'
            continue

        # get the forward candidate
        cur_d, cur_dist = cand_list_list[j][k]

        tmp_sum = cur_sum + cur_dist
        if tmp_sum > best_sum:
            # nothing in this branch can do better than the current best
            j -= 1
            k_stack.pop()
            cur_sum_stack.pop()
            if j >= 0:
                cur_back.pop()
            # print 'total bail'
            # print '-------------------------'
            continue

        # advance the counter in the k_stack, the next time this level
        # of the frame stack is run the _next_ candidate will be run
        k_stack[-1] += 1
        # check if it's already linked
        if cur_d is not None and cur_d in cur_back:
            # this will run the loop with almost identical stack, but with advanced k
            # print 'already linked cur_d'
            # print '-------------------------'
            continue

        j += 1
        k_stack.append(0)
        cur_sum_stack.append(tmp_sum)
        cur_back.append(cur_d)

        # print '-------------------------'
    #    print 'done'
    return source_list, best_back

def numba_link(s_sn, dest_size, search_radius):
    """Recursively find the optimal bonds for a group of particles between 2 frames.

    This is only invoked when there is more than one possibility within
    ``search_radius``.

    Note that ``dest_size`` is unused; it is determined from the contents of
    the source list.
    """
    # The basic idea: replace Point objects with integer indices into lists of Points.
    # Then the hard part (recursion) runs quickly because it is just passing arrays.
    # In fact, we can compile it with numba so that it runs in acceptable time.
    max_candidates = 9 # Max forward candidates we expect for any particle
    src_net = list(s_sn)
    nj = len(src_net) # j will index the source particles
    if nj > SubnetLinker.MAX_SUB_NET_SIZE:
        raise SubnetOversizeException('search_range (aka maxdisp) too large for reasonable performance on these data (sub net contains %d points)' % nj)
    # Build arrays of all destination (forward) candidates and their distances
    dcands = set()
    for p in src_net:
        dcands.update([cand for cand, dist in p.forward_cands])
    dcands = list(dcands)
    dcands_map = {cand: i for i, cand in enumerate(dcands)}
    # A source particle's actual candidates only take up the start of
    # each row of the array. All other elements represent the null link option
    # (i.e. particle lost)
    candsarray = np.ones((nj, max_candidates + 1), dtype=np.int64) * -1
    distsarray = np.ones((nj, max_candidates + 1), dtype=np.float64) * search_radius
    ncands = np.zeros((nj,), dtype=np.int64)
    for j, sp in enumerate(src_net):
        ncands[j] = len(sp.forward_cands)
        if ncands[j] > max_candidates:
            raise SubnetOversizeException('search_range (aka maxdisp) too large for reasonable performance on these data (particle has %i forward candidates)' % ncands[j])
        candsarray[j,:ncands[j]] = [dcands_map[cand] for cand, dist in sp.forward_cands]
        distsarray[j,:ncands[j]] = [dist for cand, dist in sp.forward_cands]
    # The assignments are persistent across levels of the recursion
    best_assignments = np.ones((nj,), dtype=np.int64) * -1
    cur_assignments = np.ones((nj,), dtype=np.int64) * -1
    tmp_assignments = np.zeros((nj,), dtype=np.int64)
    cur_sums = np.zeros((nj,), dtype=np.float64)
    # In the next line, distsarray is passed in quadrature so that adding distances works.
    bestsum = _numba_subnet_norecur(ncands, candsarray, distsarray**2, cur_assignments, cur_sums,
            tmp_assignments, best_assignments)
    if bestsum < 0:
        raise SubnetOversizeException('search_range (aka maxdisp) too large for reasonable performance on these data (exceeded max iterations for subnet)')
    # Return particle objects. Account for every source particle we were given.
    # 'None' denotes a null link and will be used for the memory feature.
    return zip(*[(src_net[j], (dcands[i] if i >= 0 else None)) \
            for j, i in enumerate(best_assignments)])

@try_numba_autojit
def _numba_subnet_norecur(ncands, candsarray, dists2array, cur_assignments, cur_sums, tmp_assignments, best_assignments):
    """Find the optimal track assigments for a subnetwork, without recursion.

    This is for nj source particles. All arguments are arrays with nj rows.

    cur_assignments, tmp_assignments are just temporary registers of length nj.
    best_assignments is modified in place.
    Returns the best sum.
    """
    itercount = 0
    nj = candsarray.shape[0]
    tmp_sum = 0.
    best_sum = 1.0e23
    j = 0
    while 1:
        itercount += 1
        if itercount >= 500000000:
            return -1.0
        delta = 0 # What to do at the end
        # This is an endless loop. We go up and down levels of recursion,
        # and emulate the mechanics of nested "for" loops, using the
        # blocks of code marked "GO UP" and "GO DOWN". It's not pretty.

        # Load state from the "stack"
        i = tmp_assignments[j]
        #if j == 0:
        #    print i, j, best_sum
        #    sys.stdout.flush()
        if i > ncands[j]:
            # We've exhausted possibilities at this level, including the
            # null link; make no more changes and go up a level
            #### GO UP
            delta = -1
        else:
            tmp_sum = cur_sums[j] + dists2array[j,i]
            if tmp_sum > best_sum:
                # if we are already greater than the best sum, bail. we
                # can bail all the way out of this branch because all
                # the other possible connections (including the null
                # connection) are more expensive than the current
                # connection, thus we can discard with out testing all
                # leaves down this branch
                #### GO UP
                delta = -1
            else:
                # We have to seriously consider this candidate.
                # We can have as many null links as we want, but the real particles are finite
                # This loop looks inefficient but it's what numba wants!
                flag = 0
                for jtmp in range(nj):
                    if cur_assignments[jtmp] == candsarray[j,i]:
                        if jtmp < j:
                            flag = 1
                if flag and candsarray[j,i] >= 0:
                    # we have already used this destination point; try the next one instead
                    delta = 0
                else:
                    cur_assignments[j] = candsarray[j,i]
                    # OK, I guess we'll try this assignment
                    if j + 1 == nj:
                        # We have made assignments for all the particles,
                        # and we never exceeded the previous best_sum.
                        # This is our new optimum.
                        #print 'hit: %f' % best_sum
                        best_sum = tmp_sum
                        # This array is shared by all levels of recursion.
                        # If it's not touched again, it will be used once we
                        # get back to link_subnet
                        for tmpj in range(nj):
                            best_assignments[tmpj] = cur_assignments[tmpj]
                        #### GO UP
                        delta = -1
                    else:
                        # Try various assignments for the next particle
                        #### GO DOWN
                        delta = 1
        if delta == -1:
            if j > 0:
                j += -1
                tmp_assignments[j] += 1 # Try the next candidate at this higher level
                continue
            else:
                return best_sum
        elif delta == 1:
            j += 1
            cur_sums[j] = tmp_sum # Floor for all subsequent sums
            tmp_assignments[j] = 0
        else:
            tmp_assignments[j] += 1

def _maybe_remove(s, p):
    # Begging forgiveness is faster than asking permission
    try:
        s.remove(p)
    except KeyError:
        pass

sub_net_linker = SubnetLinker  # legacy
Hash_table = HashTable  # legacy
