# Copyright (c) Meta Platforms, Inc. and affiliates

import copy
from typing import Dict, Tuple

from torch.distributed._shard.checkpoint.metadata import (
    STATE_DICT_TYPE,
)
from torch.distributed._shard.sharded_tensor import (
    Shard,
    ShardMetadata,
    ShardedTensor
)

from .utils import (
    traverse_state_dict,
    set_element,
    element_wise_add,
)

def flatten_sharded_tensors(state_dict: Dict[str, Any]) -> Dict[str, Any]:
    new_state_dict = {}

    def rewrite_dict(path, value):
        if not isinstance(value, ShardedTensor):
            set_element(new_state_dict, path, value)
            return
        shards = value.local_shards()
        if len(shards) == 0:
            return
        if len(shards) != 1:
            raise ValueError(f"Cannot handle outer tensor with more than 1 shard {path} -- {len(shards)}")
        outer_shard = shards[0]

        inner_st = outer_shard.tensor
        if not isinstance(inner_st, ShardedTensor):
            set_element(new_state_dict, path, value)
            return

        if len(inner_st.local_shards()) != 1:
            raise ValueError("Cannot handle inner tensor with more than 1 shard")
        inner_shard = inner_st.local_shards()[0]

        local_shards = [
            Shard(
                tensor=inner_shard.tensor,
                metadata=ShardMetadata(
                    shard_offsets=element_wise_add(
                        outer_shard.metadata.shard_offsets, 
                        inner_shard.metadata.shard_offsets),
                    shard_sizes=inner_shard.metadata.shard_sizes,
                    placement=f"rank:{dist.get_rank()}/{inner_shard.tensor.device}"
                ))
        ]

        st_meta: ShardedTensorMetadata = copy.deepcopy(value.metadata())
        other_rank = 0 if dist.get_rank() > 0 else 1
        # Remove the outer ST shard the inner ST covers
        for i, shard_md in enumerate(st_meta.shards_metadata):
            if shard_md.shard_offsets == outer_shard.metadata.shard_offsets:
                st_meta.shards_metadata.pop(i)
                break

        # blame other rank for the other shards
        for shard_md in st_meta.shards_metadata:
            shard_md.placement=_remote_device(f"rank:{other_rank}/cuda:0")

        # Add other inner shards from the inner tensor
        for inner_md in inner_st.metadata().shards_metadata:
            if inner_md.shard_offsets != inner_shard.metadata.shard_offsets:
                st_meta.shards_metadata.append(ShardMetadata(
                    shard_offsets=element_wise_add(
                        outer_shard.metadata.shard_offsets, 
                        inner_md.shard_offsets),
                    shard_sizes=inner_md.shard_sizes,
                    placement=f"rank:{other_rank}/cuda:0"
                ))
        
        #finally add this shard
        st_meta.shards_metadata.append(local_shards[0].metadata)
        
        st = ShardedTensor._init_from_local_shards_and_global_metadata(
            local_shards=local_shards,
            sharded_tensor_metadata=st_meta,
        )
        set_element(new_state_dict, path, st)

    traverse_state_dict(state_dict, rewrite_dict)
    return new_state_dict
