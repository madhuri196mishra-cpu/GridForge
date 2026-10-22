"""Optional CAD serialization backends.

Backend modules must remain isolated from Core and import CAD dependencies lazily.
"""
from .oda_file_converter import ODAFileConverterBackend

__all__ = ["ODAFileConverterBackend"]
