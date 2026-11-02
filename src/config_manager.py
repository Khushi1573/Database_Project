# src/config_manager.py
def validate_cell(cell_name, cell_data):
    errors = []
    engine = cell_data.get('engine')
    index = cell_data.get('index')
    ahi = cell_data.get('ahi')

    # Your specific validation rules
    if engine not in ['InnoDB', 'MEMORY']:
        errors.append(f"Cell {cell_name}: Unsupported engine '{engine}'")
    
    if index not in ['BTREE', 'HASH']:
        errors.append(f"Cell {cell_name}: Unsupported index '{index}'")

    if engine == 'MEMORY' and ahi != 'n/a':
        errors.append(f"Cell {cell_name}: AHI setting not allowed on MEMORY cells")

    if engine == 'InnoDB' and ahi not in ['on', 'off']:
        errors.append(f"Cell {cell_name}: AHI must be 'on' or 'off' for InnoDB")

    return errors