"""mRNAspect: mRNA quality control from one nanopore direct RNA run.

The QC library behind the mRNAspect app and command line. ``__version__`` is stamped
into every result file as ``software_version`` (see ``provenance``).
"""

__version__ = "0.1.0"

#: The product's name wherever a person reads it: the report, --help, messages.
#: Environment variables use the ``QCRNA_`` prefix; renaming one breaks existing setups.
PRODUCT_NAME = "mRNAspect"
