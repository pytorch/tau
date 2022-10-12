# Copyright (c) Meta Platforms, Inc. and affiliates
import torch
from typing import List, Union
from spmd.tensor.api import DTensor


# pyre-fixme[3]: Return type must be annotated.
# pyre-fixme[2]: Parameter must be annotated.
def unwrap_single_placement(e):
    if not isinstance(e, DTensor):
        return None
    assert len(e.placements) == 1, "more than one placement!"
    return e.placements[0]


# convenient wrapper to register custom operator impls
# pyre-fixme[3]: Return type must be annotated.
# pyre-fixme[2]: Parameter must be annotated.
def register_impl(func):
    # pyre-fixme[53]: Captured variable `func` is not annotated.
    # pyre-fixme[3]: Return type must be annotated.
    # pyre-fixme[2]: Parameter must be annotated.
    def wrapper(impl):
        DTensor._custom_dispatch_ops[func] = impl
        return impl

    return wrapper


# convenient wrapper to register sharding propagation rules
# pyre-fixme[3]: Return type must be annotated.
# pyre-fixme[2]: Parameter must be annotated.
def register_prop_rule(func):
    # pyre-fixme[53]: Captured variable `func` is not annotated.
    # pyre-fixme[3]: Return type must be annotated.
    # pyre-fixme[2]: Parameter must be annotated.
    def wrapper(impl):
        DTensor._op_to_rules[func] = impl
        return impl

    return wrapper


def as_list(x: Union[List[object], object]) -> Union[List[object], object]:
    # During tracing, `aten.sum.dim_IntList` used `immutable_list` for its args,
    # which is an object but treated as a list by the. Therefore, keep
    # `immutable_list` intact here as well.
    if type(x) is list or isinstance(
        x, torch.fx.immutable_collections.immutable_list
    ):
        return x
    else:
        return [x]
