# Copyright (c) Meta Platforms, Inc. and affiliates

# Minimum effort to run this example:
# $ torchrun --nproc-per-node 4 pippy_fnet.py

import argparse
import os

import torch
import torch.distributed as dist

from pippy import pipeline
from pippy import SplitPoint, annotate_split_points
from pippy.PipelineSchedule import ScheduleGPipe
from pippy.PipelineStage import PipelineStage

from transformers import FNetForMaskedLM, FNetConfig

from hf_utils import generate_inputs_for_model, get_number_of_params


def add_split_points(fnet, nranks):
    layers_per_rank = fnet.config.num_hidden_layers // nranks
    for i in range(1, nranks):
        annotate_split_points(
            fnet, {f"fnet.encoder.layer.{i * layers_per_rank}": SplitPoint.BEGINNING})


def run(args):
    # Model configs
    config = FNetConfig()
    print("Using device:", args.device)

    # Create model
    model_class = FNetForMaskedLM
    model_name = "FNetForMaskedLM"
    fnet = model_class(config)
    fnet.to(args.device)
    fnet.eval()
    if args.rank == 0:
        print(fnet.config)
        print(f"Total number of params = {get_number_of_params(fnet) // 10 ** 6}M")
        print(fnet)

    # Input configs
    example_inputs = generate_inputs_for_model(
        model_class, fnet, model_name, args.batch_size, args.device)
    input_ids = example_inputs["input_ids"]

    # Annotate split points
    add_split_points(fnet, args.world_size)

    # Create pipeline
    fnet_pipe = pipeline(
        fnet,
        num_chunks=args.chunks,
        example_args=(input_ids, ),
    )

    assert fnet_pipe.num_stages == args.world_size, f"nstages = {fnet_pipe.num_stages} nranks = {args.world_size}"
    if args.rank == 0:
        for i, sm in enumerate(fnet_pipe.split_gm.children()):
            print(f"Pipeline stage {i} {get_number_of_params(sm) // 10 ** 6}M params")

    # Create schedule runtime
    stage = PipelineStage(
        fnet_pipe,
        args.rank,
        device=args.device,
    )

    # Attach to a schedule
    schedule = ScheduleGPipe(stage, args.chunks)

    # Run
    if args.rank == 0:
        schedule.step(input_ids)
    else:
        out = schedule.step()

    print(f"Rank {args.rank} completes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--world_size', type=int, default=int(os.getenv("WORLD_SIZE", 4)))
    parser.add_argument('--rank', type=int, default=int(os.getenv("RANK", -1)))
    parser.add_argument('--master_addr', type=str, default=os.getenv('MASTER_ADDR', 'localhost'))
    parser.add_argument('--master_port', type=str, default=os.getenv('MASTER_PORT', '29500'))
    parser.add_argument('--schedule', type=str, default="FillDrain")
    parser.add_argument('--cuda', type=int, default=int(torch.cuda.is_available()))
    parser.add_argument("--chunks", type=int, default=4)
    parser.add_argument('--batch_size', type=int, default=32)
    parser.add_argument('--batches', type=int, default=1)

    args = parser.parse_args()

    if args.cuda:
        dev_id = args.rank % torch.cuda.device_count()
        args.device = torch.device(f"cuda:{dev_id}")
    else:
        args.device = torch.device("cpu")

    # Init process group
    backend = "nccl" if args.cuda else "gloo"
    dist.init_process_group(
        backend=backend,
        rank=args.rank,
        world_size=args.world_size,
    )

    run(args)
