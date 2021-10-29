from ctapipe.core import Provenance
from protozfits import File

__all__ = ['MultiFiles']


class MultiFiles:
    """
    This class open all the files in file_list and read the events following
    the event_id order
    """

    def __init__(self, paths):

        paths = list(paths)
        if len(paths) == 0:
            raise ValueError('`paths` must not be empty')

        self._file = {}
        self._events = {}
        self._events_table = {}
        self._camera_config = {}


        for path in paths:
            Provenance().add_input_file(path, role='r0.sub.evt')

            try:
                self._file[path] = File(str(path))
                self._events_table[path] = self._file[path].Events
                self._events[path] = next(self._file[path].Events)

                if hasattr(self._file[path], 'CameraConfig'):
                    self._camera_config[path] = next(self._file[path].CameraConfig)

            except StopIteration:
                pass

        # verify that we found a CameraConfig
        if len(self._camera_config) == 0:
            raise IOError(f"No CameraConfig was found in any of the input files: {paths}")
        else:
            self.camera_config = next(iter(self._camera_config.values()))

    def close(self):
        for f in self._file.values():
            f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next_event()

    def next_event(self):
        # check for the minimal event id
        if not self._events:
            raise StopIteration

        min_path = min(
            self._events.items(),
            key=lambda item: item[1].event_id,
        )[0]

        # return the minimal event id
        next_event = self._events[min_path]
        try:
            self._events[min_path] = next(self._file[min_path].Events)
        except StopIteration:
            del self._events[min_path]

        return next_event

    def __len__(self):
        total_length = sum(
            len(table)
            for table in self._events_table.values()
        )
        return total_length

    def rewind(self):
        for file in self._file.values():
            file.Events.protobuf_i_fits.rewind()

    def num_inputs(self):
        return len(self._file)
