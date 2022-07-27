# Copyright (c) Meta Platforms, Inc. and affiliates
import torch
from torch.testing._internal.common_utils import run_tests
from ..test_utils import DistTensorTestBase, with_comms
from spmd import distribute_tensor, DeviceMesh, Tensor, Shard, Replicate, _Partial
from torch.distributed.distributed_c10d import ReduceOp


class DistTensorOpsTest(DistTensorTestBase):
    @with_comms
    def test_addmm(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        shard_spec = [Shard(0)]
        replica_spec = [Replicate()]

        tensor_to_shard = torch.randn(12, 8)
        mat1 = distribute_tensor(tensor_to_shard, device_mesh, shard_spec)
        tensor_to_replicate = torch.randn(8, 4)
        mat2 = distribute_tensor(tensor_to_replicate, device_mesh, replica_spec)
        input_tensor = torch.randn(4)
        input = distribute_tensor(input_tensor, device_mesh, replica_spec)

        dist_res = torch.addmm(input, mat1, mat2)
        local_res = torch.addmm(
            input_tensor, tensor_to_shard, tensor_to_replicate
        )
        self.assertEqual(
            dist_res.redistribute(device_mesh, replica_spec).local_tensor(),
            local_res,
        )

    @with_comms
    def test_mm(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        shard_spec = [Shard(0)]
        replica_spec = [Replicate()]

        tensor_to_shard = torch.randn(12, 8, requires_grad=True)
        mat1 = distribute_tensor(tensor_to_shard, device_mesh, shard_spec)
        tensor_to_replicate = torch.randn(8, 16, requires_grad=True)
        mat2 = distribute_tensor(tensor_to_replicate, device_mesh, replica_spec)

        dist_res = torch.mm(mat1, mat2)
        local_res = torch.mm(tensor_to_shard, tensor_to_replicate)
        self.assertEqual(
            dist_res.redistribute(device_mesh, replica_spec).local_tensor(),
            local_res,
        )

        # backward
        grad_res = torch.ones(12, 16)
        grad_dist_res = distribute_tensor(grad_res, device_mesh, shard_spec)
        dist_res.backward(grad_dist_res)
        print(mat1.grad)
        # dist_res.sum().backward()

    @with_comms
    def test_sum(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        shard_spec = [Shard(0)]
        replica_spec = [Replicate()]

        tensor_to_sum = torch.randn(12, 8)
        sumed_tensor = tensor_to_sum.sum()
        mat1 = distribute_tensor(tensor_to_sum, device_mesh, shard_spec)
        dt_sum = mat1.sum()
        self.assertEqual(dt_sum.local_tensor(), sumed_tensor)

    @with_comms
    def test_t(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        shard_spec = [Shard(0)]
        replica_spec = [Replicate()]

        tensor_to_transpose = torch.randn(12, 8, requires_grad=True)
        mat = distribute_tensor(tensor_to_transpose, device_mesh, shard_spec)
        tranposed_mat = mat.t()
        self.assertEqual(tranposed_mat.size(), torch.Size([8, 12]))
        self.assertEqual(tranposed_mat.placements, [Shard(1)])
        tranposed_mat2 = tranposed_mat.t()
        self.assertEqual(tranposed_mat2.size(), torch.Size([12, 8]))
        self.assertEqual(tranposed_mat2.placements, shard_spec)

    @with_comms
    def test_detach(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        shard_spec = [Shard(0)]

        tensor_to_detach = torch.randn(12, 8, requires_grad=True)
        mat = distribute_tensor(tensor_to_detach, device_mesh, shard_spec)
        detached_mat = mat.detach()
        self.assertFalse(detached_mat is mat)

    @with_comms
    def test_ones_like(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        shard_spec = [Shard(0)]
        replica_spec = [Replicate()]

        input_tensor = torch.randn(4, 8, requires_grad=True)
        dist_tensor = Tensor.from_local(input_tensor, device_mesh, shard_spec)
        ones_like_dt = torch.ones_like(dist_tensor)
        ones_expected = torch.ones(4, 8)
        self.assertEqual(ones_expected, ones_like_dt.local_tensor())

    def _run_sharded_elementwise_ops(
        self, mesh, spec, input_size, op, **kwargs
    ):
        torch.manual_seed(self.rank)
        input_tensor = torch.randn(*input_size, requires_grad=True)
        dist_tensor = Tensor.from_local(input_tensor, mesh, spec)
        dt = op(dist_tensor, **kwargs)
        expected = op(input_tensor, **kwargs)
        self.assertEqual(input_tensor, dist_tensor.local_tensor())
        if "training" in kwargs and kwargs["training"]:
            self.assertNotEqual(expected, dt.local_tensor())
        else:
            self.assertEqual(expected, dt.local_tensor())

    @with_comms
    def test_activations(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        self._run_sharded_elementwise_ops(device_mesh, [Shard(0)], (8, 5), torch.nn.functional.gelu)
        self._run_sharded_elementwise_ops(device_mesh, [Replicate()], (8, 5), torch.nn.functional.gelu)
        self._run_sharded_elementwise_ops(device_mesh, [Shard(1)], (3, 14), torch.nn.functional.relu)
        self._run_sharded_elementwise_ops(device_mesh, [Replicate()], (8, 5), torch.nn.functional.relu)

    @with_comms
    def test_dropout(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        self._run_sharded_elementwise_ops(device_mesh, [Shard(0)], (8, 5),
                                          torch.nn.functional.dropout, p=0.4, training=False)
        self._run_sharded_elementwise_ops(device_mesh, [Shard(1)], (3, 14),
                                          torch.nn.functional.dropout, p=0.5, training=True)

    @with_comms
    def test_dropout_errors(self):
        device_mesh = DeviceMesh(self.device_type, list(range(self.world_size)))
        with self.assertRaisesRegex(RuntimeError, 'Not supported!'):
            self._run_sharded_elementwise_ops(device_mesh, [_Partial(ReduceOp.SUM)], (8, 5),
                                              torch.nn.functional.dropout, p=0.4, training=False)


if __name__ == "__main__":
    run_tests()
