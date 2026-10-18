"""End-to-end tests of the bronze → silver → gold pipeline over synthetic sources.

:mod:`.sources` holds the synthetic adapters; ``python -m composer_e2e <args>``
is ``composer-ingest <args>`` with those adapters registered in place of the
real ones, so a test drives the real CLI, bucket, databases and passes — only
the network is gone.
"""
