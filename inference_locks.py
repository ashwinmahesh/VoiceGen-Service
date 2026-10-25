"""Pass existing reservations to native descendants without reopening locks.

Context is local to the inference thread. Descriptors remain owned by the
manager/data-owner; subprocess pass_fds duplicates their lifetime into the
native child and its time wrapper, including if the Python parent is killed.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_reservations = ContextVar('voice_inference_reservations',default=())


@contextmanager
def inherit_reservations(*handles):
    descriptors = tuple(dict.fromkeys(handle.fileno() for handle in handles if handle is not None))
    token = _reservations.set(descriptors)
    try:
        yield
    finally:
        _reservations.reset(token)


def reservation_descriptors():
    return _reservations.get()
