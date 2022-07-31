from typing import List, Optional
from spmd.tensor.placement_types import Shard, Replicate, _Partial, PlacementSpec

[
    "abs",
    "absolute",
    "acos",
    "arccos",
    "acosh",
    "arccosh",
    "add",
    "addcdiv",
    "addcmul",
    "angle",
    "asin",
    "arcsin",
    "asinh",
    "arcsinh",
    "atan",
    "arctan",
    "atanh",
    "arctanh",
    "atan2",
    "arctan2",
    "bitwise_not",
    "bitwise_and",
    "bitwise_or",
    "bitwise_xor",
    "bitwise_left_shift",
    "bitwise_right_shift",
    "ceil",
    "clamp",
    "clip",
    "conj_physical",
    "copysign",
    "cos",
    "cosh",
    "deg2rad",
    "div",
    "divide",
    "digamma",
    "erf",
    "erfc",
    "erfinv",
    "exp",
    "exp2",
    "expm1",
    "fake_quantize_per_channel_affine",
    "fake_quantize_per_tensor_affine",
    "fix",
    "float_power",
    "floor",
    "floor_divide",
    "fmod",
    "frac",
    "frexp",
    "gradient",
    "imag",
    "ldexp",
    "lerp",
    "lgamma",
    "log",
    "log10",
    "log1p",
    "log2",
    "logaddexp",
    "logaddexp2",
    "logical_and",
    "logical_not",
    "logical_or",
    "logical_xor",
    "logit",
    "hypot",
    "i0",
    "igamma",
    "igammac",
    "mul",
    "multiply",
    "mvlgamma",
    "nan_to_num",
    "neg",
    "negative",
    "nextafter",
    "polygamma",
    "positive",
    "pow",
    "quantized_batch_norm",
    "quantized_max_pool1d",
    "quantized_max_pool2d",
    "rad2deg",
    "real",
    "reciprocal",
    "remainder",
    "round",
    "rsqrt",
    "sigmoid",
    "sign",
    "sgn",
    "signbit",
    "sin",
    "sinc",
    "sinh",
    "sqrt",
    "square",
    "sub",
    "subtract",
    "tan",
    "tanh",
    "true_divide",
    "trunc",
    "xlogy",
]


def pointwise_rules(input: List[PlacementSpec]) -> Optional[PlacementSpec]:
    pass
