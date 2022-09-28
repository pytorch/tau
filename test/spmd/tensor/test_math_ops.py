# Copyright (c) Meta Platforms, Inc. and affiliates
import itertools

import torch
from torch.testing._internal.common_distributed import skip_if_lt_x_gpu
from torch.testing._internal.common_utils import run_tests

from spmd import DeviceMesh, Shard, distribute_tensor
from spmd.tensor.placement_types import Replicate
from spmd.testing.common_utils import TEST_GPU_NUM  # type: ignore
from spmd.testing.common_utils import DistTensorTestBase, with_comms


class DistMathOpsTest(DistTensorTestBase):
    @with_comms
    def test_sum(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        shard_spec = [Shard(0)]

        tensor_to_sum = torch.randn(12, 8, 8)

        mat1 = distribute_tensor(tensor_to_sum, device_mesh, shard_spec)

        for dim in range(tensor_to_sum.ndim):
            dim_sumed_tensor = tensor_to_sum.sum(dim=dim)
            dt_dim_sumed_tensor = mat1.sum(dim=dim).redistribute(
                device_mesh, [Replicate()] * device_mesh.ndim
            )
            self.assertEqual(dt_dim_sumed_tensor.to_local(), dim_sumed_tensor)

        full_sumed_tensor = tensor_to_sum.sum()
        dt_sum = mat1.sum().redistribute(
            device_mesh, [Replicate()] * device_mesh.ndim
        )
        self.assertEqual(dt_sum.to_local(), full_sumed_tensor)

    # TODO: forward test can be removed once test_softmax_with_bwd passes on CPU
    @with_comms
    def test_softmax_fwd(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        x = torch.rand(8, 12, 16, device=self.device_type)
        dims = range(3)  # used to convert -1 to the actual dim
        softmax_dims = [-1, 0, 1, 2]
        shard_dims = [-1, 0, 1, 2]
        test_list = list(itertools.product(softmax_dims, shard_dims))

        for softmax_dim, shard_dim in test_list:
            local_y = torch.nn.functional.softmax(
                x, dim=softmax_dim, dtype=torch.float32
            )
            dist_x = distribute_tensor(x, device_mesh, [Shard(shard_dim)])
            if dims[shard_dim] == dims[softmax_dim]:
                with self.assertRaisesRegex(
                    Exception, "Cannot run .* on sharding dimension!$"
                ):
                    dist_y = torch.nn.functional.softmax(
                        dist_x, dim=softmax_dim, dtype=torch.float32
                    )
            else:
                dist_y = torch.nn.functional.softmax(
                    dist_x, dim=softmax_dim, dtype=torch.float32
                )
                self.assertTrue(dist_y.placements[0].is_shard(dim=shard_dim))
                dist_y = dist_y.redistribute(device_mesh, [Replicate()])
                self.assertEqual(dist_y.to_local(), local_y)

    # TODO: get test_softmax_with_bwd pass on CPU
    # DTensor's _softmax_backward_data produces wrong result on CPU on certain dimension.
    # fail_on_cpu_list = [(0, -1), (1, -1)]
    @with_comms
    @skip_if_lt_x_gpu(TEST_GPU_NUM)
    def test_softmax_with_bwd(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        dims = range(3)  # used to convert -1 to the actual dim
        softmax_dims = [-1, 0, 1, 2]
        shard_dims = [-1, 0, 1, 2]
        test_list = list(itertools.product(softmax_dims, shard_dims))

        for params in test_list:
            softmax_dim, shard_dim = params
            x = torch.rand(
                8, 12, 16, device=self.device_type, requires_grad=True
            )
            self.assertTrue(x.requires_grad)
            local_y = torch.nn.functional.softmax(
                x, dim=softmax_dim, dtype=torch.float32
            ).sum()
            local_y.backward()

            dist_x = distribute_tensor(x, device_mesh, [Shard(shard_dim)])
            self.assertTrue(dist_x.requires_grad)
            if dims[softmax_dim] == dims[shard_dim]:
                with self.assertRaisesRegex(
                    Exception, "Cannot run .* on sharding dimension!$"
                ):
                    dist_softmax = dist_x.softmax(dim=softmax_dim)
            else:
                dist_softmax = dist_x.softmax(dim=softmax_dim)
                self.assertTrue(
                    dist_softmax.placements[0].is_shard(dim=shard_dim)
                )
                dist_y = dist_softmax.sum()
                dist_y = dist_y.redistribute(device_mesh, [Replicate()])
                self.assertEqual(dist_y.to_local(), local_y)
                self.assertIsNone(dist_x.grad)
                dist_y.backward()
                self.assertIsNotNone(dist_x.grad)
                dist_x_grad = dist_x.grad.redistribute(
                    device_mesh, [Replicate()]
                )
                self.assertEqual(dist_x_grad.to_local(), x.grad)


if __name__ == "__main__":
    run_tests()
