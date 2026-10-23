"""Errors shared by every kind of device."""


class DeviceRefused(Exception):
    """The device answered, but refused the request (for Modbus: an exception reply)."""


class FrameError(Exception):
    """A reply that is malformed or does not answer the request."""
