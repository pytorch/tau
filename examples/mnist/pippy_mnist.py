# Copyright (c) Meta Platforms, Inc. and affiliates
import argparse
import logging
import os
from functools import reduce

import torch
from torch import nn, optim
from torch.nn.functional import cross_entropy
from torch.utils.data import DistributedSampler
from torchvision import datasets, transforms  # type: ignore
from tqdm import tqdm  # type: ignore

import pippy.fx
from pippy import run_pippy
from pippy.IR import MultiUseParameterConfig, Pipe, PipeSplitWrapper, LossWrapper
from pippy.PipelineDriver import PipelineDriverFillDrain, PipelineDriver1F1B, PipelineDriverInterleaved1F1B, \
    PipelineDriverBase
from pippy.events import EventsContext
from pippy.microbatch import CustomReducer, TensorChunkSpec
from pippy.visualizer import events_to_json

PROFILING_ENABLED = True
CHECK_NUMERIC_EQUIVALENCE = True

schedules = {
    'FillDrain': PipelineDriverFillDrain,
    '1F1B': PipelineDriver1F1B,
    'Interleaved1F1B': PipelineDriverInterleaved1F1B,
}

VERBOSE = bool(int(os.environ.get('VERBOSE', False)))

if VERBOSE:
    logging.getLogger().setLevel(logging.DEBUG)

pippy.fx.Tracer.proxy_buffer_attributes = True

USE_TQDM = bool(int(os.getenv('USE_TQDM', '1')))


def resolve_pg_per_stage(pp_rank):
    assert pippy.utils.dp_pg_per_pp_rank
    return pippy.utils.dp_pg_per_pp_rank[pp_rank + 1]  # exclude master


def run_master(pp_ranks, args):
    torch.manual_seed(42)
    MULTI_USE_PARAM_CONFIG = MultiUseParameterConfig.REPLICATE if args.replicate else MultiUseParameterConfig.TRANSMIT
    print(f'REPLICATE config: {args.replicate} -> {MULTI_USE_PARAM_CONFIG}')
    print("Using schedule:", args.schedule)
    print("Using device:", args.device)

    number_of_workers = 3
    all_worker_ranks = pp_ranks[1:1 + number_of_workers]  # exclude master
    chunks = len(all_worker_ranks)
    batch_size = args.batch_size * chunks

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])

    train_data = datasets.MNIST('./data', train=True, download=True, transform=transform)
    valid_data = datasets.MNIST('./data', train=False, transform=transform)

    train_sampler = DistributedSampler(train_data, num_replicas=args.dp_group_size, rank=args.rank, shuffle=False,
                                       drop_last=False)

    train_dataloader = torch.utils.data.DataLoader(train_data, batch_size=batch_size, sampler=train_sampler)
    valid_dataloader = torch.utils.data.DataLoader(valid_data, batch_size=batch_size)

    class OutputLossWrapper(LossWrapper):
        def __init__(self, module, loss_fn):
            super().__init__(module, loss_fn)

        def forward(self, input, target):
            output = self.module(input)
            return output, self.loss_fn(output, target)

    model = nn.Sequential(
        nn.Flatten(),
        nn.Linear(28 * 28, 128),
        nn.ReLU(),
        PipeSplitWrapper(nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
        )),
        PipeSplitWrapper(nn.Linear(64, 10))
    )

    wrapper = OutputLossWrapper(model, cross_entropy)

    pipe = Pipe.from_tracing(wrapper, MULTI_USE_PARAM_CONFIG, output_loss_value_spec=(False, True))
    pipe.to(args.device)

    args_chunk_spec = (TensorChunkSpec(0), TensorChunkSpec(0))
    kwargs_chunk_spec = {}
    output_chunk_spec = (TensorChunkSpec(0), CustomReducer(torch.tensor(0.0), lambda a, b: a + b))
    pipe_driver: PipelineDriverBase = schedules[args.schedule](pipe, chunks, args_chunk_spec, kwargs_chunk_spec,
                                                               output_chunk_spec,
                                                               world_size=len(all_worker_ranks),
                                                               all_ranks=all_worker_ranks,
                                                               _debug_mask_minibatches=False,
                                                               _record_mem_dumps=bool(args.record_mem_dumps),
                                                               checkpoint=bool(args.checkpoint))

    pipe_driver.init_data_parallel(dp_group_size=args.dp_group_size, dp_pg_cb=resolve_pg_per_stage)

    optimizer = pipe_driver.instantiate_optimizer(optim.Adam, lr=1e-3, betas=(0.9, 0.999), eps=1e-8)
    lr_sched = pipe_driver.instantiate_lr_scheduler(optim.lr_scheduler.LinearLR, verbose=VERBOSE)

    loaders = {
        "train": train_dataloader,
        "valid": valid_dataloader
    }

    this_file_name = os.path.splitext(os.path.basename(__file__))[0]
    pipe_visualized_filename = f"{this_file_name}_visualized_{args.rank}.json"
    batches_events_contexts = []

    for epoch in range(args.max_epochs):
        print(f"Epoch: {epoch + 1}")
        for k, dataloader in loaders.items():
            epoch_correct = 0
            epoch_all = 0
            for i, (x_batch, y_batch) in enumerate(tqdm(dataloader) if USE_TQDM else dataloader):
                x_batch = x_batch.to(args.device)
                y_batch = y_batch.to(args.device)
                if k == "train":
                    pipe_driver.train()
                    optimizer.zero_grad()
                    outp, _ = pipe_driver(x_batch, y_batch)
                    preds = outp.argmax(-1)
                    correct = (preds == y_batch).sum()
                    all = len(y_batch)
                    epoch_correct += correct.item()
                    epoch_all += all
                    optimizer.step()
                else:
                    pipe_driver.eval()
                    with torch.no_grad():
                        outp, _ = pipe_driver(x_batch, y_batch)
                        preds = outp.argmax(-1)
                        correct = (preds == y_batch).sum()
                        all = len(y_batch)
                        epoch_correct += correct.item()
                        epoch_all += all

                if args.visualize:
                    batches_events_contexts.append(pipe_driver.retrieve_events())
            print(f"Loader: {k}. Accuracy: {epoch_correct / epoch_all}")

            if k == "train":
                lr_sched.step()
                if VERBOSE:
                    print(f"Pipe {pp_ranks} last_lr: {lr_sched.get_last_lr()}")
                    print(f"Pipe {pp_ranks} state_dict: {lr_sched.state_dict()}")

    if args.visualize:
        all_events_contexts: EventsContext = reduce(lambda c1, c2: EventsContext().update(c1).update(c2),
                                                    batches_events_contexts, EventsContext())
        with open(pipe_visualized_filename, "w") as f:
            f.write(events_to_json(all_events_contexts))
        print(f"Saved {pipe_visualized_filename}")
    print('Finished')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--world_size', type=int, default=int(os.getenv("WORLD_SIZE", 4)))
    parser.add_argument('--rank', type=int, default=int(os.getenv("RANK", -1)))
    parser.add_argument('--master_addr', type=str, default=os.getenv('MASTER_ADDR', 'localhost'))
    parser.add_argument('--master_port', type=str, default=os.getenv('MASTER_PORT', '29500'))

    parser.add_argument('--max_epochs', type=int, default=10)
    parser.add_argument('--batch_size', type=int, default=10)

    parser.add_argument('-s', '--schedule', type=str, default=list(schedules.keys())[0], choices=schedules.keys())
    parser.add_argument('--replicate', type=int, default=int(os.getenv("REPLICATE", '0')))
    parser.add_argument('--cuda', type=int, default=int(torch.cuda.is_available()))
    parser.add_argument('--visualize', type=int, default=0, choices=[0, 1])
    parser.add_argument('--record_mem_dumps', type=int, default=0, choices=[0, 1])
    parser.add_argument('--checkpoint', type=int, default=0, choices=[0, 1])
    parser.add_argument('--exclude_master', type=int, default=0, choices=[0, 1])
    args = parser.parse_args()

    args.pp_group_size = 4

    assert args.world_size % args.pp_group_size == 0

    args.dp_group_size = args.world_size // args.pp_group_size

    run_pippy(run_master, args)
