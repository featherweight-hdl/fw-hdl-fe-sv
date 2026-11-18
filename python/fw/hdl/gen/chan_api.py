"""``<prefix>_req_if``/``<prefix>_rsp_if``: the channel APIs a kit derives
from a bundle of channels (``Kit.bundles``), declared as interface
classes. The other generators then derive bases, listeners, proxies and
imps for them as for any API."""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from . import irb
from .kit import Kit, KitApi


def decls(api: KitApi, kit: Kit) -> List[ir.DataType]:
    if not api.derived:
        return []
    return [irb.template(api.name, api.tmpl, api.proto)]
