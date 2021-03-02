from collections import deque, defaultdict
import numpy as np

import astropy.version
from astropy.io.ascii import convert_numpy
from astropy.table import Table
from astropy.time import Time, TimeUnixTai, TimeFromEpoch

from ctapipe.core import TelescopeComponent
from ctapipe.core.traits import IntTelescopeParameter, TelescopeParameter
from ctapipe.containers import NAN_TIME

from traitlets import Enum, Int as _Int, Bool


if astropy.version.major == 4 and astropy.version.minor <= 2 and astropy.version.bugfix <= 0:
    # clear the cache to not depend on import orders
    TimeFromEpoch.__dict__['_epoch']._cache.clear()
    # fix for astropy #11245, epoch was wrong by 8 seconds
    TimeUnixTai.epoch_val = '1970-01-01 00:00:00.0'
    TimeUnixTai.epoch_scale = 'tai'



CENTRAL_MODULE = 132


# fix for https://github.com/ipython/traitlets/issues/637
class Int(_Int):
    def validate(self, obj, value):
        if value is None and self.allow_none is True:
            return value

        return super().validate(obj, value)


def calc_dragon_time(lst_event_container, module_index, reference):
    return (
        reference
        + lst_event_container.evt.pps_counter[module_index]
        + lst_event_container.evt.tenMHz_counter[module_index] * 1e-7
    )


def calc_tib_time(lst_event_container, reference):
    return (
        reference
        + lst_event_container.evt.tib_pps_counter
        + lst_event_container.evt.tib_tenMHz_counter * 1e-7
    )


def datetime_cols_to_time(date, time):
    return Time(np.char.add(
        date,
        np.char.add('T', time)
    ))


def read_night_summary(path):
    '''
    Read a night summary file into an astropy table

    Parameters
    ----------
    path: str or Path
        Path to the night summary file

    Returns
    -------
    table: Table
        astropy table of the night summary file.
        The columns will have the correct dtype (int64) for the counters
        and missing values (nan in the file) are masked.
    '''

    # convertes for each column to make sure we use the correct
    # dtypes. The counter values in ns are so large that they cannot
    # be exactly represented by float64 values, we need int64
    converters = {
        'run': [convert_numpy(np.int32)],
        'n_subruns': [convert_numpy(np.int32)],
        'run_type': [convert_numpy(str)],
        'date': [convert_numpy(str)],
        'time': [convert_numpy(str)],
        'first_valid_event_dragon': [convert_numpy(np.int64)],
        'ucts_t0_dragon': [convert_numpy(np.int64)],
        'dragon_counter0': [convert_numpy(np.int64)],
        'first_valid_event_tib': [convert_numpy(np.int64)],
        'ucts_t0_tib': [convert_numpy(np.int64)],
        'tib_counter0': [convert_numpy(np.int64)],
    }

    summary = Table.read(
        str(path),
        format='ascii.basic',
        delimiter=' ',
        header_start=0,
        data_start=0,
        names=[
            'run', 'n_subruns', 'run_type', 'date', 'time',
            'first_valid_event_dragon', 'ucts_t0_dragon', 'dragon_counter0',
            'first_valid_event_tib', 'ucts_t0_tib', 'tib_counter0',
        ],
        converters=converters,
        fill_values=("nan", -1),
        guess=False,
        fast_reader=False,
    )

    summary.add_index(['run'])
    summary['timestamp'] = datetime_cols_to_time(summary['date'], summary['time'])
    return summary


class EventTimeCalculator(TelescopeComponent):
    '''
    Class to calculate event times from low-level counter information.

    Also keeps track of "UCTS jumps", where UCTS info goes missing for
    a certain event and all following info has to be shifted.
    '''

    timestamp = TelescopeParameter(
        trait=Enum(['ucts', 'dragon', 'tib']), default_value='dragon'
    ).tag(config=True)

    ucts_t0_dragon = TelescopeParameter(
        Int(allow_none=True),
        default_value=None,
        help='UCTS timestamp of a valid ucts/dragon counter combination'
    ).tag(config=True)

    dragon_counter0 = TelescopeParameter(
        Int(allow_none=True),
        help='Dragon board counter value of a valid ucts/dragon counter combination',
        default_value=None,
    ).tag(config=True)

    ucts_t0_tib = TelescopeParameter(
        Int(allow_none=True),
        default_value=None,
        help='UCTS timestamp of a valid ucts/tib counter combination'
    ).tag(config=True)

    tib_counter0 = TelescopeParameter(
        Int(allow_none=True),
        default_value=None,
        help='TIB board counter value of a valid ucts/tib counter combination'
    ).tag(config=True)

    dragon_module_id = IntTelescopeParameter(
        default_value=CENTRAL_MODULE,
        help='Module id used to calculate dragon time.',
    ).tag(config=True)

    use_first_event = Bool(default_value=True).tag(config=True)

    def __init__(self, subarray, config=None, parent=None, **kwargs):
        '''Initialize EventTimeCalculator'''
        super().__init__(subarray=subarray, config=config, parent=parent, **kwargs)

        self.previous_ucts_timestamps = defaultdict(deque)
        self.previous_ucts_trigger_types = defaultdict(deque)

        self._has_tib_reference = {}
        self._has_dragon_reference = {}

        # we cannot __setitem__ telescope lookup values, so we store them
        # in non-trait private values
        self._ucts_t0_dragon = {}
        self._dragon_counter0 = {}
        self._ucts_t0_tib = {}
        self._tib_counter0 = {}

        for tel_id in self.subarray.tel:
            self._has_dragon_reference[tel_id] = (
                self.ucts_t0_dragon.tel[tel_id] is not None
                and self.dragon_counter0.tel[tel_id] is not None
            )

            if self._has_dragon_reference[tel_id]:
                self._ucts_t0_dragon[tel_id] = self.ucts_t0_dragon.tel[tel_id]
                self._dragon_counter0[tel_id] = self.dragon_counter0.tel[tel_id]


            self._has_tib_reference[tel_id] = (
                self.ucts_t0_tib.tel[tel_id] is not None
                and self.tib_counter0.tel[tel_id] is not None
            )
            if self._has_tib_reference:
                self._ucts_t0_tib[tel_id] = self.ucts_t0_tib.tel[tel_id]
                self._tib_counter0[tel_id] = self.tib_counter0.tel[tel_id]

            if (
                (self.timestamp == "dragon" and not self._has_dragon_reference[tel_id])
                or (self.timestamp == "tib" and not self._has_tib_reference[tel_id])
            ):
                if not self.use_first_event:
                    raise ValueError(
                        'No external reference timestamps/counter values provided'
                        ' and ``use_first_event`` is False'
                    )
                else:
                    self.log.warning(
                        'Using first event as time reference for counters,'
                        ' this will lead to wrong timestamps / trigger types'
                        ' for all but the first subrun'
                    )

    def __call__(self, tel_id, event):
        lst = event.lst.tel[tel_id]

        # data comes in random module order, svc contains actual order
        module_index = np.where(lst.svc.module_ids == self.dragon_module_id.tel[tel_id])[0][0]

        tib_available = lst.evt.extdevices_presence & 1
        ucts_available = lst.evt.extdevices_presence & 2

        if not ucts_available:
            self.log.warning(
                f'Cannot calculate timestamp for obs_id={event.index.obs_id}'
                f', event_id={event.index.event_id}, tel_id={tel_id}. UCTS unavailable.'
            )
            return NAN_TIME


        ucts_timestamp = lst.evt.ucts_timestamp
        ucts_time = ucts_timestamp * 1e-9
        tib_time = np.nan
        dragon_time = np.nan

        # first event and values not passed
        if not self._has_dragon_reference[tel_id] and not self._has_tib_reference[tel_id]:
            initial_dragon_counter = (
                int(1e9) * lst.evt.pps_counter[module_index]
                + 100 * lst.evt.tenMHz_counter[module_index]
            )

            self._ucts_t0_dragon[tel_id] = ucts_timestamp
            self._dragon_counter0[tel_id] = initial_dragon_counter
            self.log.critical(
                'Using first event as time reference for dragon.'
                f' UCTS timestamp: {ucts_timestamp}'
                f' dragon_counter: {initial_dragon_counter}'
            )
            dragon_time = ucts_time
            self._has_dragon_reference[tel_id] = True

            if not tib_available and self.timestamp == 'tib':
                raise ValueError(
                    'TIB is selected for timestamp, no external reference given'
                    ' and first event has not TIB info'
                )

            if tib_available:
                initial_tib_counter = (
                    int(1e9) * lst.evt.tib_pps_counter
                    + 100 * lst.evt.tib_tenMHz_counter
                )
                self._ucts_t0_tib[tel_id] = ucts_timestamp
                self._tib_counter0[tel_id] = initial_tib_counter
                self.log.critical(
                    'Using first event as time reference for TIB.'
                    f' UCTS timestamp: {ucts_timestamp}'
                    f' tib_counter: {initial_tib_counter}'
                )

                tib_time = ucts_time
                self._has_tib_reference[tel_id] = True
        else:
            if self._has_dragon_reference[tel_id]:
                # Dragon/TIB timestamps based on a valid absolute reference UCTS timestamp
                dragon_time = calc_dragon_time(
                    lst, module_index,
                    reference=1e-9 * (self._ucts_t0_dragon[tel_id] - self._dragon_counter0[tel_id])
                )

            if self._has_tib_reference[tel_id] and tib_available:
                tib_time = calc_tib_time(
                    lst,
                    reference=1e-9 * (self._ucts_t0_tib[tel_id] - self._tib_counter0[tel_id])
                )

        # Due to a DAQ bug, sometimes there are 'jumps' in the
        # UCTS info in the raw files. After one such jump,
        # all the UCTS info attached to an event actually
        # corresponds to the next event. This one-event
        # shift stays like that until there is another jump
        # (then it becomes a 2-event shift and so on). We will
        # keep track of those jumps, by storing the UCTS info
        # of the previously read events in the list
        # previous_ucts_time_unix. The list has one element
        # for each of the jumps, so if there has been just
        # one jump we have the UCTS info of the previous
        # event only (which truly corresponds to the
        # current event). If there have been n jumps, we keep
        # the past n events. The info to be used for
        # the current event is always the first element of
        # the array, previous_ucts_time_unix[0], whereas the
        # current event's (wrong) ucts info is placed last in
        # the array. Each time the first array element is
        # used, it is removed and the rest move up in the
        # list. We have another similar array for the trigger
        # types, previous_ucts_trigger_type
        ucts_trigger_type = lst.evt.ucts_trigger_type

        if len(self.previous_ucts_timestamps[tel_id]) > 0:
            # put the current values last in the queue, for later use:
            self.previous_ucts_timestamps[tel_id].append(ucts_timestamp)
            self.previous_ucts_trigger_types[tel_id].append(ucts_trigger_type)

            # get the correct time for the current event from the queue
            ucts_timestamp = self.previous_ucts_timestamps[tel_id].popleft()
            ucts_trigger_type = self.previous_ucts_trigger_types[tel_id].popleft()
            ucts_time = ucts_timestamp * 1e-9

            lst.evt.ucts_trigger_type = ucts_trigger_type
            lst.evt.ucts_timestamp = ucts_timestamp

        # Now check consistency of UCTS and Dragon times. If
        # UCTS time is ahead of Dragon time by more than
        # 1.e-6 s, most likely the UCTS info has been
        # lost for this event (i.e. there has been another
        # 'jump' of those described above), and the one we have
        # actually corresponds to the next event. So we put it
        # back first in the list, to assign it to the next
        # event. We also move the other elements down in the
        # list,  which will now be one element longer.
        # We leave the current event with the same time,
        # which will be approximately correct (depending on
        # event rate), and set its ucts_trigger_type to -1,
        # which will tell us a jump happened and hence this
        # event does not have proper UCTS info.
        if (ucts_time - dragon_time) > 1e-6:
            self.log.warning(
                f'Found UCTS jump in event {event.index.event_id}'
                f', dragon time: {dragon_time:.07f}'
                f', delta: {(ucts_time - dragon_time) * 1e6:.1f} µs'
            )
            self.previous_ucts_timestamps[tel_id].appendleft(ucts_timestamp)
            self.previous_ucts_trigger_types[tel_id].appendleft(ucts_trigger_type)

            # fall back to dragon time / tib trigger
            ucts_time = dragon_time
            lst.evt.ucts_timestamp = int(dragon_time * 1e9)

            if tib_available:
                lst.evt.ucts_trigger_type = lst.evt.tib_masked_trigger
            else:
                self.log.warning(
                    'Detected ucts jump but not tib trigger info available'
                    ', event will have no trigger information'
                )
                lst.evt.ucts_trigger_type = 0

        # Select the timestamps to be used for pointing interpolation
        if self.timestamp.tel[tel_id] == "ucts":
            timestamp = Time(ucts_time, format='unix_tai')

        elif self.timestamp.tel[tel_id] == "dragon":
            timestamp = Time(dragon_time, format='unix_tai')

        elif self.timestamp.tel[tel_id] == "tib":
            timestamp = Time(tib_time, format='unix_tai')
        else:
            raise ValueError('Unknown timestamp requested')

        self.log.debug(f'tib: {tib_time:.7f}, dragon: {dragon_time:.7f}, ucts: {ucts_time:.7f}')

        return timestamp
