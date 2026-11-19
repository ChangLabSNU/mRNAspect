"""Real-time QC: feed Core from a live MinKNOW run directory.

The offline driver (:mod:`mrnaspect.main`) reads a fixed list of inputs; this package
watches a MinKNOW output folder instead and feeds the same Core (``AnalysisEngine``, the
QC modules, ``CoAReporter``). Given the same inputs, both drivers must produce identical
``snapshot()`` and ``finalize()`` numbers.
"""
