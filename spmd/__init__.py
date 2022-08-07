# Copyright (c) Meta Platforms, Inc. and affiliates
from typing import Sequence, Optional
import torch
import torch.nn as nn
from spmd.tensor import DTensor, Placement, Shard, Replicate, _Partial
from spmd.tensor.device_mesh import get_global_device_mesh, DeviceMesh

torch.__future__.set_overwrite_module_params_on_conversion(True)


def distribute_tensor(
    tensor: torch.Tensor,
    device_mesh: Optional[DeviceMesh] = None,
    placements: Optional[Sequence[Placement]] = None,
) -> DTensor:
    """
    Distribute a torch.Tensor to the `device_mesh` according to the `placements`
    specified. The rank of `device_mesh` and `placements` must be the same.

    Args:
        tensor (torch.Tensor): torch.Tensor to be distributed
        device_mesh (:class:`DeviceMesh`, optional): DeviceMesh to distribute the
            tensor, if not specified, must be called under a DeviceMesh context
            manager, default: None
        placements (List[:class:`Placement`], optional): the placements that
            describes how to place the tensor on DeviceMesh, must have the same
            number of elements as `device_mesh.ndim`. If not specified, we will
            by default replicate the tensor across the `device_mesh` from the
            first rank of each dimension of the `device_mesh`.

    Returns:
        A :class:`DTensor` object
    """
    # get default device mesh if there's nothing specified
    device_mesh = (
        get_global_device_mesh() if device_mesh is None else device_mesh
    )
    # convert tensor to the correponding device type if it's not in that device type
    tensor = tensor.to(device_mesh.device_type)
    # set default placements to replicated if not specified
    if placements is None:
        placements = [Replicate() for _ in range(device_mesh.ndim)]

    # distribute the tensor according to PlacementSpec
    assert len(placements) == 1, "Only support 1-d placement now"
    for idx, placement in enumerate(placements):
        if isinstance(placement, Shard):
            shard_dim = placement.dim
            assert (
                shard_dim <= tensor.ndim
            ), "Sharding dim {shard_dim} greater than tensor ndim {tensor.ndim}"
            # TODO: handle multi-dim device mesh and last shard
            num_chunks = device_mesh.size()
            assert tensor.size(shard_dim) % num_chunks == 0, (
                f"Only support chunk sharding evenly now, but tensor got "
                f"dimension {shard_dim} of size {tensor.size(shard_dim)}, "
                f"which does not divide number of shards {num_chunks}."
            )
            chunk_size = tensor.size(shard_dim) // num_chunks
            tensor_list = list(tensor.chunk(num_chunks, dim=shard_dim))
            scatter_shape = list(tensor.size())
            scatter_shape[shard_dim] = chunk_size
            local_tensor = device_mesh.scatter(tensor_list)
            dist_tensor = DTensor(
                local_tensor,
                device_mesh,
                placements,
                requires_grad=local_tensor.requires_grad,
            )
        elif isinstance(placement, Replicate):
            device_mesh.broadcast(tensor, mesh_dim=idx)
            dist_tensor = DTensor(
                tensor,
                device_mesh,
                placements,
                requires_grad=tensor.requires_grad,
            )
        else:
            raise RuntimeError("Not supported!")

    # pyre-fixme[61]: `dist_tensor` is undefined, or not always defined.
    return dist_tensor


# pyre-fixme[3]: Return type must be annotated.
def distribute_module(
    mod: nn.Module,
    device_mesh: Optional[DeviceMesh] = None,
    spec: Optional[Sequence[Placement]] = None,
):
    """
    this function coverts all module parameters
    to distributed tensor parameters according to
    the placements and device_mesh spcified.
    TODO: add a more flexible tagging, i.e. convert
    certain param to a certain spec, like a PlacementPlan
    """

    # pyre-fixme[3]: Return type must be annotated.
    # pyre-fixme[2]: Parameter must be annotated.
    def to_dist_tensor(t):
        if isinstance(t, nn.Parameter):
            return distribute_tensor(t.data, device_mesh, spec)
        else:
            return t

    mod._apply(to_dist_tensor)

    return mod
