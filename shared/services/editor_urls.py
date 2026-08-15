#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Build problem-editor return URLs that carry a tab, a query, and an anchor.

Every route that sends an author back to the tabbed editor has to say *which tab*
they land on, and a per-row route also has to say which row. Arena additionally
carries the problem list's filter state through the editor. So a return URL can
need all three parts at once, and the obvious ``f"{url}?tab=…#tc-{id}"`` is wrong
in two ways that are easy to miss:

* if ``url`` already carries a query string, a second ``?`` produces a parameter
  no server will parse;
* if ``url`` already carries a fragment, appending ``?tab=…`` puts the parameter
  *inside* the fragment, where it is silently dropped.

Both modules therefore build these URLs here rather than by concatenation.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def editor_url(base: str, *, tab: str | None = None, anchor: str | None = None) -> str:
    """Return ``base`` with a tab parameter merged in and an anchor re-attached.

    An existing ``tab`` parameter is replaced rather than duplicated, every other
    parameter is preserved in order, and the fragment is always written last.

    Args:
        base: The editor URL, which may already carry a query and/or a fragment.
        tab: Canonical tab value to select, or None to leave the query alone.
        anchor: Fragment to land on *without* its ``#``, or None to keep the
            fragment ``base`` already has.

    Returns:
        str: The combined URL.
    """
    scheme, netloc, path, query, fragment = urlsplit(base)
    if tab is not None:
        params = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key != "tab"]
        params.append(("tab", tab))
        query = urlencode(params)
    if anchor is not None:
        fragment = anchor
    return urlunsplit((scheme, netloc, path, query, fragment))
