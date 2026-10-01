"""One module per OS for `inkvault schedule`.

Each has render functions (pure text, tested on every OS) and install / remove / installed / wake_note, which call
the OS's own scheduler. They all share the signature install(argv, hour, minute, wake) -> description.
"""
