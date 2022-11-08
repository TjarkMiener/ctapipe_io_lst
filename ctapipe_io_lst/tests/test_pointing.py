import os
from pathlib import Path
import numpy as np
from astropy.time import Time
import astropy.units as u
from ctapipe.core import Provenance

test_data = Path(os.getenv('LSTCHAIN_TEST_DATA', 'test_data'))
test_drive_report = test_data / 'real/monitoring/DrivePositioning/DrivePosition_log_20200218.txt'
test_bending_report = test_data / 'real/monitoring/DrivePositioning/BendingModelCorrection_log_20220220.txt'
test_drive_report_with_bending = test_data / 'real/monitoring/DrivePositioning/DrivePosition_log_20220220.txt'


def test_read_drive_report():
    from ctapipe_io_lst.pointing import PointingSource

    drive_report = PointingSource._read_drive_report(test_drive_report)

    assert 'time' not in drive_report.colnames
    assert 'azimuth' in drive_report.colnames
    assert 'zenith' in drive_report.colnames


def test_interpolation():
    from ctapipe_io_lst.pointing import PointingSource
    from ctapipe_io_lst import LSTEventSource

    subarray = LSTEventSource.create_subarray(geometry_version=4)
    pointing_source = PointingSource(
        subarray=subarray,
        drive_report_path=test_drive_report,
    )

    time = Time('2020-02-18T21:40:21')
    # El is really zenith distance
    # Tue Feb 18 21:40:20 2020 1582062020 Az 230.834 230.819 230.849 7.75551 El 10.2514 10.2485 10.2543 0.00948548 RA 86.6333 Dec 22.0144
    # Tue Feb 18 21:40:23 2020 1582062022 Az 230.896 230.881 230.912 9.03034 El 10.2632 10.2603 10.2661 0.00948689 RA 86.6333 Dec 22.0144

    pointing = pointing_source.get_pointing_position_altaz(tel_id=1, time=time)
    expected_alt = (90 - 0.5 * (10.2514 + 10.2632)) * u.deg
    assert u.isclose(pointing.altitude, expected_alt)
    assert u.isclose(pointing.azimuth, 0.5 * (230.834 + 230.896) * u.deg)

    ra, dec = pointing_source.get_pointing_position_icrs(tel_id=1, time=time)
    assert np.isnan(ra)
    assert np.isnan(dec)


def test_bending_corrections():
    from ctapipe_io_lst.pointing import PointingSource
    corrections = PointingSource._read_bending_model_corrections(test_bending_report)
    assert corrections.colnames == ['unix_time', 'azimuth_correction', 'zenith_correction']


def test_load_position_and_bending_corrections():
    from ctapipe_io_lst.pointing import PointingSource

    Provenance().start_activity('test drive report')
    PointingSource._read_drive_report(test_drive_report_with_bending)
    inputs = Provenance().current_activity.input
    assert len(inputs) == 2
    assert inputs[0]['url'] == str(test_drive_report_with_bending.resolve())
    assert inputs[1]['url'] == str(test_bending_report.resolve())
