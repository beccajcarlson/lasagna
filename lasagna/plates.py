import pandas as pd
import numpy as np
import string

def microwells(width=None, base=(8, 12), downsample=((2, 2), (4, 4))):
    """Set zero-padding using `width`.
    """
    
    def row_col_to_well(row, col, width=1):
        fmt = r'%s%0' + str(width) + 'd' 
        return fmt % (string.ascii_uppercase[row], col + 1)

    def well_to_row_col(well):
        return string.ascii_uppercase.index(well[0]), int(well[1:]) - 1

    if width is None:
        width = int(np.ceil(base[1] / 10))
    ds = downsample
    
    rows, cols = np.meshgrid(range(base[0]), range(base[1]))

    a, b = base
    base_rows = pd.Series(rows.flatten(), name='%s_row' % (a*b)).astype(int)
    base_cols = pd.Series(cols.flatten(), name='%s_col' % (a*b)).astype(int)
    base_wells = [row_col_to_well(r, c, width=width) for r,c in zip(base_rows, base_cols)]
    base_wells = pd.Series(base_wells, name='%d_well' % (a*b))

    arr = [base_rows, base_cols, base_wells]
    for i, j in ds:
        rows = (base_rows / i).astype(int)
        cols = (base_cols / j).astype(int)
        wells = [row_col_to_well(r, c, width=width) for r,c in zip(rows, cols)]
        wells = pd.Series(wells)

        a = base[0] / i
        b = base[1] / j
        rows.name  = '%d_row'  % (a*b)
        cols.name  = '%d_col'  % (a*b)
        wells.name = '%d_well' % (a*b)

        arr += [rows, cols, wells]

    return pd.concat(arr, axis=1)


def plate_coordinate(well, site, spacing='10X', grid_shape=(7, 7)):
    site = int(site)
    spacing_96w = 9000
    if spacing == '20X':
        delta = 643
    elif spacing == '10X':
        delta = 1286
    else:
        delta = spacing

    row, col = well_to_row_col(well)
    i, j = row * spacing_96w, col * spacing_96w

    height, width = grid_shape
    i += delta * int(site / width)
    j += delta * (site % width)
    
    i -= delta * ((height - 1) / 2.) 
    j -= delta * ((width  - 1)  / 2.)

    return i, j


def remap_snake(site, rows=25, cols=25):
    """Maps site names from snake order to regular order.
    """
    import numpy as np
    grid = np.arange(rows*cols).reshape(rows, cols)
    grid[1::2] = grid[1::2, ::-1]
    site_ = grid.flat[int(site)]
    return '%d' % site_


def filter_position_list(filename, well_site_list):
    """Restrict micromanager position list to given wells and sites.
    """
    import json
    well_site_list = set(well_site_list)
    def filter_well_site(position):
        pat = '(.\d+)-Site_(\d+)'
        return re.findall(pat, position['LABEL'])[0] in well_site_list
    
    with open(filename, 'r') as fh:
        d = json.load(fh)
    
    d['POSITIONS'] = filter(filter_well_site, d['POSITIONS'])
    
    with open(filename + '.filtered.pos', 'w') as fh:
        json.dump(d, fh)


def well_to_row_col(df, in_place=False, col_to_int=True):
    if not in_place:
        df = df.copy()
    f = lambda s: int(s) if col_to_int else s

    df['row'] = [s[0] for s in df['well']]
    df['col'] = [f(s[1:]) for s in df['well']]

    return df

def add_global_xy(df):
    df = df.copy()
    wt = zip(df['well'], df['tile'])
    d = {(w,t): plate_coordinate(w, t) for w,t in set(zip(df['well'], df['tile']))}
    y, x = zip(*[d[k] for k in zip(df['well'], df['tile'])])

    if 'x' in df:
        df['global_x'] = x + df['x']
        df['global_y'] = y + df['y']
    elif 'position_i' in df:
        df['global_x'] = x + df['position_j']
        df['global_y'] = y + df['position_i']
    else:
        df['global_x'] = x
        df['global_y'] = y

    return df