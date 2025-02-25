import torch

from ._dbr.auto_trace import add_auto_observation, add_auto_convert
from ._dbr.fusion import get_module_fusion_fqns
from ._dbr.qconfig_dict_utils import normalize_object_types

from .qconfig_dict_utils import (
    get_flattened_qconfig_dict,
    convert_dict_to_ordered_dict,
)
from torch.ao.quantization.quantization_mappings import (
    get_default_static_quant_module_mappings,
)
from ._dbr.module_swap_utils import _swap_child_modules


def prepare(model, qconfig_dict, example_inputs, inplace=False, allow_list=None,
            observer_non_leaf_module_list=None,
            prepare_custom_config_dict=None,
            fuse_modules=True):
    r"""A wrapper around `torch.quantization.prepare` which prepares the
    model for quantization using dynamic tracing.

    Requires `qconfig_dict` (same format as prepare_fx) to specify the
    quantization settings. Not all functionality is supported yet.

    Requires `example_inputs` to build
    the graph before calibration or quantization aware training can proceed.

    TODO(future PR): better docblock
    """
    assert example_inputs is not None, 'example_inputs must be specified'

    for qconfig_dict_option in ('module_name_regex', 'module_name_object_type_order'):
        assert qconfig_dict_option not in qconfig_dict, \
            f'{qconfig_dict_option} option of qconfig_dict is not ' + \
            'implemented yet in define-by-run quantization'

    normalize_object_types(qconfig_dict)
    convert_dict_to_ordered_dict(qconfig_dict)
    flattened_qconfig_dict = get_flattened_qconfig_dict(qconfig_dict)
    torch.quantization.propagate_qconfig_(model, flattened_qconfig_dict)
    # TODO(future PR): QAT support

    if fuse_modules:
        # automatically fuse modules
        old_class = model.__class__
        model = add_auto_observation(model, qconfig_dict, example_inputs)
        module_fusion_fqns = get_module_fusion_fqns(model)
        if len(module_fusion_fqns):
            model = torch.quantization.fuse_modules(model, module_fusion_fqns)
        # TODO: also delete _auto_quant_staate of all children
        if hasattr(model, '_auto_quant_state'):
            del model._auto_quant_state
        # the model hierarchy might have changed during fusion, so we
        # have to delete the cached module hook types
        for k, v in model.named_modules():
            if hasattr(v, '_auto_quant_module_hook_type'):
                del v._auto_quant_module_hook_type

        model.__class__ = old_class

    # Automatically assign qconfigs for modules where the defaults do not
    # work.
    # TODO(future PR): clean this up and align with other APIs
    for name, child in model.named_modules():
        if isinstance(child, (torch.nn.Embedding, torch.nn.EmbeddingBag)):
            # pass
            # child.qconfig = torch.quantization.float_qparams_weight_only_qconfig
            # uncomment below to unbreak attention_is_all_you_need
            # TODO write up issue, maybe fix
            child.qconfig = None  # type: ignore[assignment]
        elif isinstance(child, torch.nn.LSTM):
            # TODO: fix LSTM handling in eager mode static quant and remove this
            child.qconfig = None

    model = torch.quantization.prepare(
        model, inplace, allow_list, observer_non_leaf_module_list,
        prepare_custom_config_dict)
    assert not inplace
    model = add_auto_observation(model, qconfig_dict, example_inputs)
    return model

def convert(model: torch.nn.Module) -> torch.nn.Module:
    r"""Converts a prepared DBR quantization model to a quantized form.

    TODO(future PR): better docblock
    """
    static_mappings = get_default_static_quant_module_mappings()
    # swap the modules
    _swap_child_modules(model, static_mappings)
    # add dynamic handling for quants/dequants, functions and methods
    model = add_auto_convert(model)
    return model
