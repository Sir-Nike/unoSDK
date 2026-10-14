"""unoSDK participant components (standard library only).

- ``protocol``: vendored wire helpers (builders, validators, bounds).
- ``adapter``: the newline-JSON lifecycle loop (initialize -> act* -> game_over).
- ``schemas/``: vendored JSON Schemas for the wire contract.

Your strategy lives in ``strategy.py`` as ``select_action``.
"""
