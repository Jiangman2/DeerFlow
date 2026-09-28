"""Bound image-graph validation work, not runtime snapshotter capabilities.

The audited enterprise baseline has 345 layers. Retain a finite 1024-layer
work bound while leaving index fanout, OCI identity, byte and blob checks
unchanged in the original verifier. Host Python 3.7+.
"""

import re

MAX_IMAGE_LAYERS = 1024
DIFF_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")


def validate_layer_graph(content, config):
    prefix = "Invalid image layer graph: "
    layers = content.get("layers")
    if not isinstance(layers, list):
        raise ValueError(prefix + "layers_not_array")
    if not 1 <= len(layers) <= MAX_IMAGE_LAYERS:
        raise ValueError(
            prefix
            + "layer_count_out_of_range count=%s limit=%s"
            % (len(layers), MAX_IMAGE_LAYERS)
        )
    rootfs = config.get("rootfs")
    if not isinstance(rootfs, dict):
        raise ValueError(prefix + "rootfs_not_object")
    if rootfs.get("type") != "layers":
        raise ValueError(prefix + "rootfs_type_not_layers")
    diff_ids = rootfs.get("diff_ids")
    if not isinstance(diff_ids, list):
        raise ValueError(prefix + "diff_ids_not_array")
    if len(diff_ids) != len(layers):
        raise ValueError(
            prefix
            + "layer_diff_id_count_mismatch layers=%s diff_ids=%s"
            % (len(layers), len(diff_ids))
        )
    for index, value in enumerate(diff_ids):
        if not isinstance(value, str) or DIFF_ID.fullmatch(value) is None:
            raise ValueError(prefix + "invalid_diff_id index=%s" % index)
    return layers
