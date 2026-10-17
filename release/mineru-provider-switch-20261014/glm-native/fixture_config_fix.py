"""Repair only missing pointer-bearing leaves in the isolated test config."""
import copy


def corrected_config(original):
    cfg = copy.deepcopy(original)
    if cfg.get('extract') != {'extract_graph': {}, 'extract_entity': {}, 'fabri_text': {}}:
        raise RuntimeError('fixture_extract_shape_changed')
    if cfg.get('conversation', {}).get('summary') != {}:
        raise RuntimeError('fixture_summary_shape_changed')
    # Viper flattens settings by scalar leaves. An all-empty nested map disappears
    # before mapstructure decodes it, leaving *ExtractManagerConfig nil. Even an
    # empty STRING leaf is retained and allocates the nested pointers correctly.
    cfg['extract'] = {
        'extract_graph': {'description': ''},
        'extract_entity': {'description': ''},
        'fabri_text': {'with_tag': '', 'with_no_tag': ''},
    }
    # Same default used by knowledge_process.go for an absent Summary config.
    cfg['conversation']['summary'] = {'max_input_chars': 16384}
    return cfg
