import sys
sys.path.append('C:/Users/LabAdmin/Documents/GitHub/lasagna')
import json
import re
import inspect
import fire
from collections import defaultdict
import functools
import os

if sys.version_info.major == 2:
    # python 2
    import numpy as np
    import pandas as pd
    import skimage
    import lasagna.bayer
    import lasagna.process
    import lasagna.io
    read = lasagna.io.read_stack
    save = lasagna.io.save_stack 

def load_csv(f):
    with open(f, 'r') as fh:
        txt = fh.readline()
    sep = ',' if ',' in txt else '\s+'
    return pd.read_csv(f, sep=sep)


def load_pkl(f):
    return pd.read_pickle(f)


def load_tif(f):
    return read(f)


def save_csv(f, df):
    df.to_csv(f, index=None)


def save_pkl(f, df):
    df.to_pickle(f)


def save_tif(f, data_, **kwargs):
    kwargs = restrict_kwargs(kwargs, save)
    # make sure `data` doesn't come from the Snake method since it's an
    # argument name for the save function, too
    kwargs['data'] = data_
    save(f, **kwargs)


def restrict_kwargs(kwargs, f):
    f_kwargs = set(get_kwarg_defaults(f).keys()) | set(get_arg_names(f))
    keys = f_kwargs & set(kwargs.keys())
    return {k: kwargs[k] for k in keys}


def load_file(f):
    # python 2, unicode coming from python 3
    if not isinstance(f, (str, unicode)):
        raise TypeError
    if not os.path.isfile(f):
        raise ValueError
    if f.endswith('.tif'):
        return load_tif(f)
    elif f.endswith('.pkl'):
        return load_pkl(f)
    elif f.endswith('.csv'):
        return load_csv(f)
    else:
        raise ValueError(f)


def load_arg(x):
    one_file = load_file
    many_files = lambda x: map(load_file, x)
    
    for f in one_file, many_files:
        try:
            return f(x)
        except (ValueError, TypeError) as e:
            # print(x)
            # print(e)
            pass
    else:
        return x


def save_output(f, x, inputs):
    """Saves a single output file. Can extend to list if needed.
    Saving .tif might use kwargs (luts, ...) from input.
    """
    if f.endswith('.tif'):
        return save_tif(f, x, **inputs)
    elif f.endswith('.pkl'):
        return save_pkl(f, x)
    elif f.endswith('.csv'):
        return save_csv(f, x)
    else:
        raise ValueError('not a recognized filetype: ' + f)


def get_arg_names(f):
    argspec = inspect.getargspec(f)
    if argspec.defaults is None:
        return argspec.args
    n = len(argspec.defaults)
    return argspec.args[:-n]


def get_kwarg_defaults(f):
    argspec = inspect.getargspec(f)
    if argspec.defaults is None:
        return {}
    defaults = {k: v for k,v in zip(argspec.args[::-1], argspec.defaults[::-1])}
    return defaults


def call_from_fire(f):
    """Turn a function that acts on a mix of image data, table data and other 
    arguments and may return image or table data into a function that acts on 
    filenames for image and table data, and json-encoded values for other arguments.

    If output filename is provided, saves return value of function.

    Supported filetypes are .pkl, .csv, and .tif.
    """
    def g(input_json=None, output=None):
        
        with open(input_json, 'r') as fh:
            inputs = json.load(fh)

        # remove unused keyword arguments
        # would be better to remove only the output arguments so 
        # incorrectly named arguments raise a sensible error
        inputs = restrict_kwargs(inputs, f)

        # provide all arguments as keyword arguments
        kwargs = {x: load_arg(inputs[x]) for x in inputs}
        try:
            kwargs['wildcards']['tile'] = int(kwargs['wildcards']['tile'])
        except KeyError:
            pass
        result = f(**kwargs)

        if output:
            save_output(output, result, inputs)

    return functools.update_wrapper(g, f)


class Snake():
    @staticmethod
    def add_method(class_, name, f):
        f = staticmethod(f)
        exec('%s.%s = f' % (class_, name))

    @staticmethod
    def load_methods():
        methods = inspect.getmembers(Snake)
        for name, f in methods:
            if name not in ('__doc__', '__module__') and name.startswith('_'):
                Snake.add_method('Snake', name[1:], call_from_fire(f))

    @staticmethod
    def _stitch(data, tile_config):
        from lasagna.pipelines._20171031 import load_tile_configuration, parse_filename

        _, ij = load_tile_configuration(tile_config)
        positions = []
        for file in files:
            site = re.findall('Site[-_](\d+)', file)[0]
            site = int(site)
            positions += [ij[site]]
    
        data = np.array(data)
        if data.ndim == 3:
            data = data[:, None]

        arr = []
        for c in range(data.shape[1]):
            result = lasagna.process.alpha_blend(data[:, c], positions)
            arr += [result]
        stitched = np.array(arr)

        return stitched

    @staticmethod
    def _align(data, index_align=0, channel_offsets=None):
        """Align data using first channel. If data is a list of stacks with different 
        IJ dimensions, the data will be piled first. Optional channel offset.
        Images are aligned to the image at `index_align`.

        """

        # shapes might be different if stitched with different configs
        # keep shape consistent with DO

        shape = data[0].shape
        data = lasagna.utils.pile(data)
        data = data[..., :shape[-2], :shape[-1]]

        indices = range(len(data))
        indices.pop(index_align)
        indices_fwd = [index_align] + indices
        indices_rev = np.argsort(indices_fwd)
        aligned = lasagna.process.register_and_offset(data[indices_fwd], registration_images=data[indices_fwd,0])
        aligned = aligned[indices_rev]
        if channel_offsets:
            aligned = fix_channel_offsets(aligned, channel_offsets)

        return aligned

    @staticmethod
    def _consensus_DO(data):
        """Use variance to estimate DO.
        """
        if data.ndim == 4:
            consensus = np.std(data[:, 1:], axis=(0, 1))
        elif data.ndim == 3:
            consensus = np.std(data[1:], axis=0)

        return consensus
    
    @staticmethod
    def _segment_nuclei(data, threshold=5000, **kwargs):
        """Find nuclei from DAPI. Find cell foreground from aligned but unfiltered 
        data. Expects data to have shape C x I x J.
        """
        nuclei = lasagna.process.find_nuclei(data[0], 
                                threshold=lambda x: threshold, **kwargs)
     
        return nuclei.astype(np.uint16)

    @staticmethod
    def _segment_cells(data, nuclei, threshold=750):
        """Segment cells from aligned data. To use less than full cycles for 
        segmentation, filter the input files.

        !!! matches cell labels to nuclei labels !!!
        """
        if data.ndim == 4:
            # no DAPI, min over cycles, mean over channels
            mask = data[:, 1:].min(axis=0).mean(axis=0)
        else:
            mask = np.median(data[1:], axis=0)

        mask = mask > threshold
        try:
            cells = lasagna.process.find_cells(nuclei, mask)
        except ValueError:
            print('segment_cells error -- no cells')
            cells = nuclei

        return cells

    @staticmethod
    def _transform_LoG(data, bsub=False):
        if data.ndim == 3:
            data = data[None]
        loged = lasagna.bayer.log_ndi(data)
        loged[..., 0, :, :] = data[..., 0, :, :] # DAPI

        if bsub:
            loged = loged - np.sort(loged, axis=0)[-2].astype(float)
            loged[loged < 0] = 0

        return loged

    @staticmethod
    def _find_peaks(data, cutoff=50):
        if data.ndim == 2:
            data = [data]
        peaks = [lasagna.process.find_peaks(x) 
                    if x.max() > 0 else x 
                    for x in data]
        peaks = np.array(peaks)
        peaks[peaks < cutoff] = 0 # for compression

        return peaks

    @staticmethod
    def _max_filter(data, width=5):
        import scipy.ndimage.filters

        if data.ndim == 3:
            data = data[None]
        maxed = np.zeros_like(data)
        maxed[:, 1:] = scipy.ndimage.filters.maximum_filter(data[:,1:], size=(1, 1, width, width))
        maxed[:, 0] = data[:, 0] # DAPI

        return maxed

    @staticmethod
    def _extract_barcodes(peaks, data_max, cells, 
        threshold_DO, cycles, wildcards, index_DO=None):
        """
        """

        if data_max.ndim == 3:
            data_max = data_max[None]
        if index_DO is None:
            index_DO = Ellipsis

        data_max = data_max[:, 1:] # no DAPI
        blob_mask = (peaks[index_DO] > threshold_DO) & (cells > 0)
        values = data_max[:, :, blob_mask].transpose([2, 0, 1])
        labels = cells[blob_mask]
        positions = np.array(np.where(blob_mask)).T

        index = ('cycle', cycles), ('channel', list('GTAC'))
        try:
            df = lasagna.utils.ndarray_to_dataframe(values, index)
        except ValueError:
            print('extract_barcodes failed to reshape, writing dummy')
            return pd.DataFrame()

        df_positions = pd.DataFrame(positions, columns=['position_i', 'position_j'])
        df = (df.stack(['cycle', 'channel'])
           .reset_index()
           .rename(columns={0:'intensity', 'level_0': 'blob'})
           .join(pd.Series(labels, name='cell'), on='blob')
           .join(df_positions, on='blob')
           )
        for k,v in wildcards.items():
            df[k] = v

        return df

    @staticmethod
    def _align_phenotype(data_DO, data_phenotype):
        """Align using DAPI.
        """
        _, offset = lasagna.process.register_images([data_DO[0], data_phenotype[0]])
        aligned = lasagna.utils.offset(data_phenotype, offset)
        return aligned

    @staticmethod
    def _segment_perimeter(data_nuclei, width=5):
        """Expand mask to generate perimeter (e.g., area around nuclei).
        """
        from lasagna.pipelines._20180302 import get_nuclear_perimeter

        return get_nuclear_perimeter(data_nuclei, width=width)

    @staticmethod
    def _extract_phenotype_FR(data_phenotype, nuclei, wildcards):
        def correlate_dapi_ha(region):
            dapi, ha = region.intensity_image_full

            filt = dapi > 0
            if filt.sum() == 0:
                # assert False
                return np.nan

            dapi = dapi[filt]
            ha  = ha[filt]
            corr = (dapi - dapi.mean()) * (ha - ha.mean()) / (dapi.std() * ha.std())

            return corr.mean()

        features = {
            'corr'       : correlate_dapi_ha,
            'dapi_median': lambda r: np.median(r.intensity_image_full[0]),
            'dapi_max'   : lambda r: r.intensity_image_full[0].max(),
            'ha_median'  : lambda r: np.median(r.intensity_image_full[1]),
            'cell'       : lambda r: r.label
        }

        return Snake._extract_phenotype(data_phenotype, nuclei, wildcards, features)       


    @staticmethod
    def _extract_phenotype_translocation_ring(data_phenotype, nuclei, wildcards, width=3):
        selem = np.ones((width, width))
        perimeter = skimage.morphology.dilation(nuclei, selem)
        perimeter[nuclei > 0] = 0

        inside = skimage.morphology.erosion(nuclei, selem)
        inner_ring = nuclei.copy()
        inner_ring[inside > 0] = 0

        return Snake._extract_phenotype_translocation(data_phenotype, inner_ring, perimeter, wildcards)

    @staticmethod
    def _extract_phenotype_translocation(data_phenotype, nuclei, cells, wildcards):
        from lasagna.pipelines._20170914_endo import feature_table_stack
        from lasagna.process import feature_table, default_object_features

        def correlate_dapi_gfp(region):
            dapi, gfp = region.intensity_image_full

            filt = dapi > 0
            if filt.sum() == 0:
                # assert False
                return np.nan

            dapi = dapi[filt]
            gfp  = gfp[filt]
            corr = (dapi - dapi.mean()) * (gfp - gfp.mean()) / (dapi.std() * gfp.std())

            return corr.mean()

        
        def masked(region, index):
            return region.intensity_image_full[index][region.filled_image]

        features_nuclear = {
            'dapi_gfp_nuclear_corr' : correlate_dapi_gfp,
            'dapi_nuclear_median': lambda r: np.median(masked(r, 0)),
            'gfp_nuclear_median' : lambda r: np.median(masked(r, 1)),
            'gfp_nuclear_mean' : lambda r: masked(r, 1).mean(),
            'dapi_nuclear_int'   : lambda r: masked(r, 0).sum(),
            'gfp_nuclear_int'    : lambda r: masked(r, 1).sum(),
            'dapi_nuclear_max'   : lambda r: masked(r, 0).max(),
            'gfp_nuclear_max'    : lambda r: masked(r, 1).max(),
            'area_nuclear'       : lambda r: r.area,
            'cell'               : lambda r: r.label
        }

        features_cell = {
            'dapi_gfp_cell_corr' : correlate_dapi_gfp,
            'gfp_cell_median' : lambda r: np.median(masked(r, 1)),
            'gfp_cell_mean' : lambda r: masked(r, 1).mean(),
            'gfp_cell_int'    : lambda r: masked(r, 1).sum(),
            'area_cell'       : lambda r: r.area,
            'cell'            : lambda r: r.label
        }

        df_n =  Snake._extract_phenotype(data_phenotype, nuclei, wildcards, features_nuclear)

        df_c =  Snake._extract_phenotype(data_phenotype, cells, wildcards, features_cell) 
        df_c = df_c[features_cell.keys()]
        
        df = (pd.concat([df_n.set_index('cell'), df_c.set_index('cell')], axis=1, join='inner')
                .reset_index())
        
        return df

    @staticmethod
    def _extract_phenotype(data_phenotype, nuclei, wildcards, features):
        from lasagna.pipelines._20170914_endo import feature_table_stack
        from lasagna.process import feature_table, default_object_features

        df = feature_table_stack(data_phenotype, nuclei, features)

        features = default_object_features.copy()
        features['cell'] = features.pop('label')
        df2 = feature_table(nuclei, nuclei, features)
        df = df.join(df2.set_index('cell'), on='cell')

        for k,v in wildcards.items():
            df[k] = v
        
        return df

###

def fix_channel_offsets(data, channel_offsets):
    d = data.transpose([1, 0, 2, 3])
    x = [lasagna.utils.offset(a, b) for a,b in zip(d, channel_offsets)]
    x = np.array(x).transpose([1, 0, 2, 3])
    return x

def stitch_input_sites(tile, site_shape, tile_shape):
    """Map tile ID onto site IDs. Fill in wildcards ourselves.
    """

    d = site_to_tile(site_shape, tile_shape)
    d2 = defaultdict(list)
    [d2[v].append(k) for k, v in d.items()]

    sites = d2[int(tile)]
    
    return sites

def site_to_tile(site_shape, tile_shape):
        """Create dictionary from site number to tile number.
        """
        result = {}
        rows_s, cols_s = site_shape
        rows_t, cols_t = tile_shape
        for i_s in range(rows_s):
            for j_s in range(cols_s):
                i_t = int(i_s * (float(rows_t) / rows_s))
                j_t = int(j_s * (float(cols_t) / cols_s))

                site = i_s * cols_s + j_s
                tile = i_t * cols_t + j_t
                result[site] = tile
        return result


if __name__ == '__main__':

    Snake.load_methods()
    fire.Fire(Snake)