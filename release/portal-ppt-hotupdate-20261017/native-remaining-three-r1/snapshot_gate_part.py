def parse_snapshot_table(raw):
    text = raw.decode('utf-8')
    require(not any(ord(char) < 32 and char != '\n' for char in text), 'snapshot_table_control_character')
    lines = text.splitlines()
    require(lines and re.fullmatch(r'KEY +PARENT +KIND *', lines[0]), 'snapshot_table_header')
    parent_column = lines[0].index('PARENT'); kind_column = lines[0].index('KIND')
    rows = {}
    for line in lines[1:]:
        require(len(line) > kind_column and line[parent_column-1] == ' ' and line[kind_column-1] == ' ', 'snapshot_table_column_shape')
        key = line[:parent_column].rstrip(' ')
        parent = line[parent_column:kind_column].rstrip(' ')
        kind = line[kind_column:].rstrip(' ')
        require(key and key == key.strip() and parent == parent.strip() and kind in ('Committed', 'Active', 'View')
                and key not in rows and len(key) <= 4096 and len(parent) <= 4096, 'snapshot_table_row_shape')
        rows[key] = dict(parent=parent, kind=kind)
    return rows


def snapshot_gate(supervisor, module, image):
    raw = module.B.capture(module.B.CTR + ['snapshots', '--snapshotter', 'overlayfs', 'ls'],
                           'ROLE_SNAPSHOT_GATE', timeout=30, limit=8*1024**2)
    rows = parse_snapshot_table(raw)
    chains = {layer['chain_id']: layer for layer in image['layers']}
    require(len(chains) == len(image['layers']) and bool(chains), 'snapshot_candidate_chain_shape')
    found = {key: row for key, row in rows.items() if key in chains}
    for key, row in found.items():
        require(row['parent'] == chains[key]['parent'], 'snapshot_candidate_parent_changed')
        require(row['kind'] == 'Committed', 'candidate_key_active_or_view_requires_review')
    prefix = 0
    for layer in image['layers']:
        if layer['chain_id'] not in found: break
        prefix += 1
    require(prefix == len(found), 'snapshot_candidate_committed_prefix_not_closed')
    parent_only_children = 0
    for key, row in rows.items():
        if row['kind'] not in ('Active', 'View'): continue
        extract = re.fullmatch(r'extract-\S+ (sha256:[a-f0-9]{64})', key)
        if extract and extract.group(1) in chains:
            raise ValueError('candidate_extraction_key_requires_review')
        if set(key.split()) & set(chains):
            raise ValueError('unclassified_candidate_key_requires_review')
        if row['parent'] in chains:
            require(re.fullmatch('[a-f0-9]{64}', key) is not None and row['kind'] == 'Active', 'unclassified_active_child_requires_review')
            require(row['parent'] in found, 'active_child_parent_not_committed')
            parent_only_children += 1
    return dict(layers=len(chains), committed_prefix=prefix, committed_total=len(found),
                active_or_view_conflict=False, already_committed=prefix == len(chains),
                ordinary_active_children_of_committed_parents=parent_only_children,
                key_and_parent_columns_distinguished=True)

