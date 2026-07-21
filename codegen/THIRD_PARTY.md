# Third-party material in `codegen/`

## `llrpdef.xml`

The core LLRP binary protocol definitions, from the LLRP Toolkit (LTK) open-source
project (<http://llrp.org/>). The file carries its own license header:
copyright Impinj, Inc. (2007) and EPCglobal Inc. (2006, 2007), licensed under the
**Apache License, Version 2.0** (<http://www.apache.org/licenses/LICENSE-2.0>), with
EPCglobal's proprietary text usable within the work by its terms. It is vendored
here unmodified as the input to `generate.py`.

The Python code generated from it (`src/llrpkit/protocol/{enums,params,messages}.py`)
is part of llrpkit and distributed under the project's MIT license.

## `impinj.xml`

Authored for llrpkit (MIT, like the rest of the project). It describes the wire
format of Impinj's Octane LLRP vendor extensions — message/parameter subtype
numbers under Impinj's IANA Private Enterprise Number 25882 and their field
layouts — which are protocol facts published by Impinj in its Octane LLRP
documentation so that third-party LLRP clients can interoperate with its readers.
Coverage focuses on the extensions llrpkit uses; it is not a copy of any Impinj
definition file.
