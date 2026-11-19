# Licence texts kept here

`make_notices.py` reads a package's licence text from the package itself. A few packages
carry none, and what they are under still has to travel with the app. Each file below is
that text, copied whole from the place named, and used only when the package offers
nothing of its own. A package that starts to ship its text makes its file here unused.

| file | used for | copied from |
| --- | --- | --- |
| `pysam.txt` | pysam (the conda package has no licence file) | `COPYING` in pysam 0.24.1's wheel, `pysam-0.24.1.dist-info/licenses/` |
| `libsqlite.txt` | libsqlite (licence "blessing": SQLite is in the public domain) | the head of `include/sqlite3.h`, as libsqlite 3.53.4 installs it |
| `GPL-3.0.txt` | the GCC runtime libraries (libgcc, libgfortran, libstdc++, libgomp), licensed "GPL-3.0-only WITH GCC-exception-3.1": their packages install the exception's text and not the GPL it modifies | `/usr/share/common-licenses/GPL-3` (Debian's copy of the FSF's text) |

Held by `mrnaspect/tests/test_third_party_notices.py`: every `.txt` here is named above.
